from flask import Flask, request, abort
import os, datetime, threading
from PIL import Image as PILImage

# ===== LINE SDK v3 =====
from linebot.v3 import WebhookHandler, ApiClient, Configuration
from linebot.v3.messaging import (
    MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent
from linebot.v3.exceptions import InvalidSignatureError

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
configuration = Configuration(access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
api_client = ApiClient(configuration)
messaging_api = MessagingApi(api_client)
blob_api = MessagingApiBlob(api_client)
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
    filepath = os.path.join(os.getcwd(), filename)

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
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "service_account.json",
        scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    return build("drive", "v3", credentials=creds)

def upload_to_drive(filepath, folder_id=None):
    service = get_drive_service()
    file_metadata = {"name": os.path.basename(filepath)}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(filepath, mimetype="application/pdf")
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()

    file_id = file["id"]

    # 公開権限を付与
    service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"}
    ).execute()

    file = service.files().get(fileId=file_id, fields="webViewLink").execute()
    return file["webViewLink"]


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

        messaging_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=f"✅ 日報PDFをGoogle Driveに保存しました！\n{drive_link}")]
            )
        )
    except Exception as e:
        messaging_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=f"❌ エラーが発生しました: {e}")]
            )
        )


# ===== テキスト処理 =====
@handler.add(MessageEvent)
def handle_message(event):
    if isinstance(event.message, TextMessageContent):
        user_id = event.source.user_id
        user_text = event.message.text.strip()

        if user_text == "日報作成":
            user_states[user_id] = {"step": 0, "answers": {}, "images": []}
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=QUESTIONS[0])]
                )
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
                messaging_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=next_q)]
                    )
                )

            elif step == len(QUESTIONS) - 1:
                print("=== 最終ステップに到達 ===")
                print("ユーザー入力:", repr(user_text))
                print("state:", state)

                if "完了" in user_text:
                    print("=== 完了を検出 ===")
                    messaging_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="📄 PDFを生成しています。しばらくお待ちください...")]
                        )
                    )
                    threading.Thread(
                        target=process_pdf_and_upload,
                        args=(user_id, state["answers"], state["images"])
                    ).start()
                    del user_states[user_id]
                else:
                    print("=== 完了以外の入力 ===")
                    messaging_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="写真を送るか「完了」と入力してください。")]
                        )
                    )


    elif isinstance(event.message, ImageMessageContent):
        user_id = event.source.user_id
        if user_id not in user_states:
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="まず「日報作成」と入力してください。")]
                )
            )
            return

        state = user_states[user_id]
        step = state["step"]

        if step == len(QUESTIONS) - 1:
            message_id = event.message.id
            content = blob_api.get_message_content(message_id)
            image_path = f"received_{message_id}.jpg"
            with open(image_path, "wb") as f:
                f.write(content)

            try:
                img = PILImage.open(image_path).convert("RGB")
                img.save(image_path, "JPEG")
            except Exception as e:
                print("画像変換エラー:", e)

            state["images"].append(image_path)
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"📷 写真を受け取りました！現在 {len(state['images'])} 枚。続けて送るか「完了」と入力してください。")]
                )
            )


if __name__ == "__main__":
    print("=== Flaskを起動します (v3版) ===")
    app.run(host="0.0.0.0", port=5000)
