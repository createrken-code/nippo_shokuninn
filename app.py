import os
from flask import Flask, request, abort

# LINE SDK v3
from linebot.v3.webhooks import WebhookHandler
from linebot.v3.messaging import MessagingApi
from linebot.v3.messaging.models import PushMessageRequest, TextMessage
from linebot.v3.exceptions import InvalidSignatureError

# Flask アプリ作成
app = Flask(__name__)

# 環境変数から取得（Render の Dashboard に設定しておく）
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# Webhook handler
handler = WebhookHandler(CHANNEL_SECRET)

# Messaging API クライアント
api = MessagingApi(channel_access_token=CHANNEL_ACCESS_TOKEN)


# Webhook エンドポイント
@app.route("/callback", methods=["POST"])
def callback():
    # LINE Signature の検証
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# --- イベントハンドラ ---
from linebot.v3.webhooks import MessageEvent
from linebot.v3.messaging.models import ReplyMessageRequest

@handler.add(MessageEvent)
def handle_message(event):
    """ユーザーからメッセージを受け取った時に返信"""
    if event.message.type == "text":
        reply_token = event.reply_token
        user_text = event.message.text

        api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=f"受け取りました: {user_text}")]
            )
        )


# --- Push メッセージ送信用のテストエンドポイント ---
@app.route("/push", methods=["GET"])
def push_message():
    """手動で Push Message を送るためのテスト"""
    user_id = os.getenv("LINE_USER_ID")  # Render に環境変数で設定しておく
    if not user_id:
        return "LINE_USER_ID が未設定です", 400

    api.push_message(
        PushMessageRequest(
            to=user_id,
            messages=[TextMessage(text="Hello from Render!")]
        )
    )
    return "Push message sent!"


# --- Render 起動用 ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
