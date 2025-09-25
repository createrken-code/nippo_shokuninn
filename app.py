from flask import Flask, request, abort, send_file
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.utils import ImageReader
from PIL import Image as PILImage
import os, datetime

app = Flask(__name__)

line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))


from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT

# 日本語フォント登録
pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))

# 日本語用スタイルを作成
japanese_normal = ParagraphStyle(
    'JapaneseNormal',
    fontName='HeiseiKakuGo-W5',
    fontSize=12,
    leading=15,
    alignment=TA_LEFT
)
japanese_title = ParagraphStyle(
    'JapaneseTitle',
    fontName='HeiseiKakuGo-W5',
    fontSize=16,
    leading=20,
    alignment=TA_LEFT
)


# ===== ユーザーごとの状態管理 =====
user_states = {}

QUESTIONS = [
    "作業者名を入力してください",
    "作業現場を入力してください",
    "作業内容を入力してください",
    "作業時間を入力してください",
    "備考を入力してください（なければ「なし」と入力）",
    "写真を送ってください（複数可、終わったら「完了」と入力）"
]

# ===== PDF生成（表形式＋写真付き） =====
def create_formatted_pdf_with_images(data_dict, image_paths=None):
    filename = f"daily_report_{datetime.date.today()}_formatted.pdf"
    filepath = os.path.join(os.getcwd(), filename)

    doc = SimpleDocTemplate(filepath, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    # タイトルと日付
    elements.append(Paragraph("日報職人 - 作業報告書", japanese_title))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"作成日: {datetime.date.today()}", japanese_normal))
    elements.append(Spacer(1, 20))

    # 表データ
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
        ("FONTSIZE", (0, 0), (-1, -1), 12),
        ("BOX", (0, 0), (-1, -1), 1, colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 30))

    # 写真一覧
    if image_paths:
        elements.append(Paragraph("写真一覧:", japanese_normal))
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

# ===== Webhook =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# ===== PDFダウンロード用 =====
@app.route("/download/<path:filename>", methods=['GET'])
def download_file(filename):
    full_path = os.path.join(os.getcwd(), filename)
    if os.path.exists(full_path):
        return send_file(full_path, as_attachment=True)
    else:
        return "File not found", 404

# ===== テキスト処理 =====
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
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
            if user_text == "完了":
                pdf_path = create_formatted_pdf_with_images(state["answers"], state["images"])
                finish_and_send_pdf(user_id, event.reply_token, pdf_path)
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="写真を送るか「完了」と入力してください。")
                )

# ===== 画像処理 =====
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
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
        message_content = line_bot_api.get_message_content(message_id)
        image_path = f"received_{message_id}.jpg"
        with open(image_path, "wb") as f:
            for chunk in message_content.iter_content():
                f.write(chunk)

        try:
            img = PILImage.open(image_path).convert("RGB")
            img.save(image_path, "JPEG")
        except Exception as e:
            print("画像変換エラー:", e)

        state["images"].append(image_path)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"写真を受け取りました！現在 {len(state['images'])} 枚。続けて送るか「完了」と入力してください。")
        )

# ===== PDF返却 =====
def finish_and_send_pdf(user_id, reply_token, pdf_path):
    file_name = os.path.basename(pdf_path)
    public_url = f"{os.getenv('PUBLIC_BASE_URL')}/download/{file_name}"
    reply_text = f"日報PDFを生成しました！こちらからダウンロードできます👇\n{public_url}"

    line_bot_api.reply_message(
        reply_token,
        TextSendMessage(text=reply_text)
    )

    del user_states[user_id]

if __name__ == "__main__":
    print("=== Flaskを起動します ===")
    app.run(host="0.0.0.0", port=5000)
