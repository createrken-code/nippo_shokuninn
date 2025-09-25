from flask import Flask, request, abort
import os, datetime, threading, json
from PIL import Image as PILImage

# ===== LINE SDK (v3.11.0, 従来スタイル) =====
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, ImageMessage, TextSendMessage

# ===== PDF生成 =====
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.enums import TA_LEFT

# ===== Google Drive API =====
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account

app = Flask(__name__)

# ===== LINE設定 =====
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# ===== PDF用フォント設定 =====
pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))

# ===== スタイル =====
japanese_normal = ParagraphStyle(
    'JapaneseNormal',
    fontName='HeiseiKakuGo-W5',
    fontSize=12,
    leading=15,
    alignment=TA_LEFT
)

# ===== ユーザー状態管理 =====
user_states = {}

QUESTIONS = [
    "作業者名を入力してください",
    "作業現場を入力してください",
    "作業内容を入力してください",
    "作業時間を入力してください",
    "備考を入力してください（なければ「なし」と入力）",
    "写真を送ってください（複数可、終わったら「完了」と入力）"
]

# ===== PDF生成 =====
def create_formatted_pdf_with_images(data_dict, image_paths=None):
    filename = f"daily_report_{datetime.date.today()}_formatted.pdf"
    filepath = os.path.join("/tmp", filename)

    doc = SimpleDocTemplate(filepath, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("日報職人 - 作業報告書", styles["Title"]))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"作成日: {datetime.date.today()}", styles["Normal"]))
    elements.append(Spacer(1, 20))

    table_data = [
        ["作業者名", data_dict.get("作業者名", "未入力")],
        ["作業現場", data_dict.get("作業現場", "未入力")],
        ["作業内容", data_dict.get("作業内容", "未入力")],
        ["作業時間", data_dict.get("作業時間", "未入力")],
        ["備考", data_dict.get("備考", "未入力")],
    ]
    table = Table(table_data, colWidths=[100, 350])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "HeiseiKakuGo-W5"),
        ("BOX", (0, 0), (-1, -1), 1, colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 30))

    if image_paths:
        elements.append(Paragraph("写真一覧:", styles["Heading2"]))
        elements.append(Spacer(1, 10))
        for img_path in image_paths:
            try:
                pil_img = PILImage.open(img_path).convert("RGB")
                temp_path = img_path + "_rgb.jpg"
                pil_img.thumbnail((300, 300))
                pil_img.save(temp_path, "JPEG")
                elements.append(Image(temp_path, width=200, height=200))
                elements.append(Spacer(1, 15))
            except Exception as e:
                print("画像処理エラー:", e)

    doc.build(elements)
    return filepath

# ===== Google Drive アップロード =====
# ===== Google Drive アップロード =====
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account
import os

import json
import tempfile

def get_drive_service():
    # Renderの環境変数からJSON文字列を読み込み
    service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    return build("drive", "v3", credentials=creds)

DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")  # ← Renderの環境変数に設定

def upload_to_drive(filepath, folder_id=None):
    service = get_drive_service()
    folder_id = folder_id or DRIVE_FOLDER_ID

    file_metadata = {"name": os.path.basename(filepath)}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(filepath, mimetype="application/pdf")

    # 共有ドライブ対応: supportsAllDrives=True を必ず付ける
    created = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink, parents",
        supportsAllDrives=True
    ).execute()

    file_id = created["id"]

    # リンク共有を有効化（これも supportsAllDrives を付ける）
    service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
        supportsAllDrives=True
    ).execute()

    got = service.files().get(
        fileId=file_id,
        fields="webViewLink",
        supportsAllDrives=True
    ).execute()

    return got["webViewLink"]


# ===== Webhook =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK', 200

# ===== 非同期PDF処理 =====
def process_pdf_and_upload(user_id, answers, images):
    try:
        pdf_path = create_formatted_pdf_with_images(answers, images)
        drive_link = upload_to_drive(pdf_path)

        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=f"✅ 日報PDFをGoogle Driveに保存しました！\n{drive_link}")
        )
    except Exception as e:
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=f"❌ エラーが発生しました: {e}")
        )

# ===== メッセージ処理 =====
@handler.add(MessageEvent)
def handle_message(event):
    if isinstance(event.message, TextMessage):
        user_id = event.source.user_id
        user_text = event.message.text.strip()

        if user_text == "日報作成":
            user_states[user_id] = {"step": 0, "answers": {}, "images": []}
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=QUESTIONS[0])
            )
            return

        if user_id in user_states:
            state = user_states[user_id]
            step = state["step"]

            if step < len(QUESTIONS) - 1:
                keys = ["作業者名", "作業現場", "作業内容", "作業時間", "備考"]
                state["answers"][keys[step]] = user_text
                state["step"] += 1
                next_q = QUESTIONS[state["step"]]
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=next_q)
                )

            elif step == len(QUESTIONS) - 1:
                if "完了" in user_text:
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text="📄 PDFを生成しています。しばらくお待ちください...")
                    )
                    threading.Thread(
                        target=process_pdf_and_upload,
                        args=(user_id, state["answers"], state["images"])
                    ).start()
                    del user_states[user_id]
                else:
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(text="写真を送るか「完了」と入力してください。")
                    )

    elif isinstance(event.message, ImageMessage):
        user_id = event.source.user_id
        if user_id not in user_states:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="まず「日報作成」と入力してください。")
            )
            return

        state = user_states[user_id]
        step = state["step"]

        if step == len(QUESTIONS) - 1:
            message_id = event.message.id
            image_path = f"/tmp/received_{message_id}.jpg"
            content = line_bot_api.get_message_content(message_id)
            with open(image_path, "wb") as f:
                for chunk in content.iter_content():
                    f.write(chunk)

            try:
                img = PILImage.open(image_path).convert("RGB")
                img.save(image_path, "JPEG")
            except Exception as e:
                print("画像変換エラー:", e)

            state["images"].append(image_path)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"📷 写真を受け取りました！現在 {len(state['images'])} 枚。続けて送るか「完了」と入力してください。")
            )

if __name__ == "__main__":
    print("=== Flaskを起動します (Render対応 v1/v3.11.0) ===")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
