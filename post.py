import os
import json
import random
import requests
import cairo
import gi
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Pango, PangoCairo
from PIL import Image
from datetime import date, datetime
import io
import base64
import time

# ── Config ────────────────────────────────────────────────────────────────────
INSTAGRAM_USER_ID  = os.environ["INSTAGRAM_USER_ID"]
INSTAGRAM_TOKEN    = os.environ["INSTAGRAM_ACCESS_TOKEN"]
GITHUB_TOKEN       = os.environ["GITHUB_TOKEN"]
GITHUB_REPO        = os.environ["GITHUB_REPO"]
TELEGRAM_TOKEN     = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
GRAPH_API_VERSION  = "v25.0"

QUEUE_FILE   = "queue.json"
PHOTOS_DIR   = "photos"

# ── Step 1: Load queue ────────────────────────────────────────────────────────
def load_queue():
    if not os.path.exists(QUEUE_FILE):
        return []
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_queue(queue):
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

def already_posted(queue, photo):
    return any(p["photo"] == photo for p in queue)

# ── Step 2: Pick random photo ─────────────────────────────────────────────────
def pick_photo(queue):
    photos = [
        f for f in os.listdir(PHOTOS_DIR)
        if f.lower().endswith(('.jpg', '.jpeg', '.png'))
        and not already_posted(queue, f)
    ]
    if not photos:
        raise RuntimeError("No unposted photos left in photos/ folder.")
    return random.choice(photos)

# ── Step 3: Generate caption with Claude vision ───────────────────────────────
def generate_caption(photo_path):
    with open(photo_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode()

    ext = photo_path.split(".")[-1].lower()
    media_type = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-opus-4-5",
            "max_tokens": 300,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_data,
                        }
                    },
                    {
                        "type": "text",
                        "text": """You are a social media manager for Al Shaam, an authentic Middle Eastern restaurant.
Look at this food photo and return ONLY a JSON object with exactly these two fields:
{
  "dish_name": "Short dish name (3-5 words max)",
  "caption": "One appetizing sentence about the dish (max 15 words)"
}
No markdown, no explanation, just the JSON."""
                    }
                ]
            }]
        }
    )
    response.raise_for_status()
    text = response.json()["content"][0]["text"].strip()
    # Clean any markdown
    text = text.replace("```json", "").replace("```", "").strip()
    data = json.loads(text)
    return data["dish_name"], data["caption"]

# ── Step 4: Generate post image ───────────────────────────────────────────────
def generate_image(photo_path, dish_name, caption):
    output_file = f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

    img = Image.open(photo_path).convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left+side, top+side)).resize((1080, 1080), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1080, 1080)
    ctx = cairo.Context(surface)
    bg = cairo.ImageSurface.create_from_png(buf)
    ctx.set_source_surface(bg, 0, 0)
    ctx.paint()

    def get_text_height(text, font, size):
        layout = PangoCairo.create_layout(ctx)
        layout.set_text(text, -1)
        layout.set_font_description(Pango.FontDescription(f"{font} {size}"))
        layout.set_width(1080 * Pango.SCALE)
        _, lh = layout.get_pixel_size()
        return lh

    def draw_centered(text, font, size, y, alpha=1.0, color=(1,1,1)):
        layout = PangoCairo.create_layout(ctx)
        layout.set_text(text, -1)
        layout.set_font_description(Pango.FontDescription(f"{font} {size}"))
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_width(1080 * Pango.SCALE)
        lw, lh = layout.get_pixel_size()
        ctx.set_source_rgba(*color, alpha)
        ctx.move_to(0, y - lh//2)
        PangoCairo.show_layout(ctx, layout)

    def draw_shadow(text, font, size, y, alpha=1.0):
        draw_centered(text, font, size, y+3, alpha=0.6, color=(0,0,0))
        draw_centered(text, font, size, y, alpha=alpha)

    # Center text block
    GAP = 28
    h1 = get_text_height(dish_name, "sans bold", 58)
    h2 = get_text_height(caption, "sans bold", 26)
    total = h1 + GAP + h2
    center_y = 490
    y1 = center_y - total//2 + h1//2
    y2 = y1 + h1//2 + GAP + h2//2

    draw_shadow(dish_name, "sans bold", 58, y1)
    draw_shadow(caption, "sans bold", 26, y2, alpha=0.95)

    # Green strip bottom
    ctx.set_source_rgba(0.176, 0.416, 0.31, 1.0)
    ctx.rectangle(0, 980, 1080, 100)
    ctx.fill()
    draw_centered("الشام", "Noto Naskh Arabic", 22, 1008)
    draw_centered("AL SHAAM", "sans bold", 16, 1048, alpha=0.85)

    # Save as JPEG
    pil_surface = Image.frombytes("RGBA", (1080, 1080),
                                   surface.get_data(), "raw", "BGRA")
    pil_surface = pil_surface.convert("RGB")
    pil_surface.save(output_file, "JPEG", quality=95)
    print(f"Image generated: {output_file}")
    return output_file

# ── Step 5: Telegram approval ─────────────────────────────────────────────────
def send_telegram(output_file, dish_name, caption):
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

    msg = f"🍽 *{dish_name}*\n_{caption}_\n\n@alshaamrestaurant"
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Post",   "callback_data": "post"},
        {"text": "🔄 Redo",  "callback_data": "redo"},
        {"text": "❌ Cancel", "callback_data": "cancel"}
    ]]}

    with open(output_file, "rb") as f:
        r = requests.post(f"{base}/sendPhoto", data={
            "chat_id":      TELEGRAM_CHAT_ID,
            "caption":      msg,
            "parse_mode":   "Markdown",
            "reply_markup": json.dumps(keyboard)
        }, files={"photo": f})
    r.raise_for_status()
    print("Sent to Telegram.")

    requests.get(f"{base}/getUpdates", params={"offset": -1, "limit": 1})
    offset = None

    for _ in range(360):
        time.sleep(10)
        params = {"timeout": 8, "allowed_updates": ["callback_query"]}
        if offset:
            params["offset"] = offset
        resp = requests.get(f"{base}/getUpdates", params=params).json()

        for update in resp.get("result", []):
            offset = update["update_id"] + 1
            cb = update.get("callback_query")
            if cb and str(cb["message"]["chat"]["id"]) == str(TELEGRAM_CHAT_ID):
                action = cb["data"]
                requests.post(f"{base}/answerCallbackQuery", json={"callback_query_id": cb["id"]})
                print(f"Response: {action}")
                return action

    return "cancel"

# ── Step 6: Upload to GitHub ──────────────────────────────────────────────────
def upload_to_github(output_file):
    with open(output_file, "rb") as f:
        content = base64.b64encode(f.read()).decode()

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{output_file}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

    r = requests.get(api_url, headers=headers)
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {"message": f"post: {date.today()}", "content": content}
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=headers, json=payload)
    r.raise_for_status()

    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{output_file}"
    print(f"Uploaded: {raw_url}")
    return raw_url

# ── Step 7: Post to Instagram ─────────────────────────────────────────────────
def post_to_instagram(image_url, caption):
    base = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{INSTAGRAM_USER_ID}"

    r = requests.post(f"{base}/media", data={
        "image_url":    image_url,
        "caption":      caption,
        "access_token": INSTAGRAM_TOKEN,
    })
    print(f"Instagram response: {r.status_code} {r.text}")
    r.raise_for_status()
    container_id = r.json()["id"]

    for _ in range(10):
        time.sleep(10)
        status = requests.get(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{container_id}",
            params={"fields": "status_code", "access_token": INSTAGRAM_TOKEN}
        ).json().get("status_code", "")
        print(f"Container status: {status}")
        if status == "FINISHED":
            break
        elif status == "ERROR":
            raise RuntimeError("Instagram container failed.")

    r = requests.post(f"{base}/media_publish", data={
        "creation_id":  container_id,
        "access_token": INSTAGRAM_TOKEN,
    })
    r.raise_for_status()
    print(f"Posted: {r.json()['id']}")

# ── Step 8: Commit queue ──────────────────────────────────────────────────────
def commit_queue(queue):
    content  = base64.b64encode(json.dumps(queue, ensure_ascii=False, indent=2).encode()).decode()
    api_url  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{QUEUE_FILE}"
    headers  = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

    r   = requests.get(api_url, headers=headers)
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {"message": f"queue: {date.today()}", "content": content}
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=headers, json=payload)
    r.raise_for_status()
    print("Queue committed.")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    queue = load_queue()
    print(f"Queue has {len(queue)} entries.")

    while True:
        photo = pick_photo(queue)
        photo_path = os.path.join(PHOTOS_DIR, photo)
        print(f"Photo picked: {photo}")

        print("Generating caption with Claude...")
        dish_name, caption = generate_caption(photo_path)
        print(f"Dish: {dish_name} | Caption: {caption}")

        output_file = generate_image(photo_path, dish_name, caption)

        action = send_telegram(output_file, dish_name, caption)

        if action == "cancel":
            print("Cancelled.")
            return

        if action == "redo":
            print("Redoing...")
            queue.append({"photo": photo, "posted_at": f"skipped_{date.today()}"})
            continue

        # Post
        image_url = upload_to_github(output_file)
        time.sleep(10)

        ig_caption = f"{dish_name}\n\n{caption}\n\n#AlShaam #MiddleEasternFood #LevantineFood #Halal #Tampa"

        queue.append({"photo": photo, "posted_at": str(date.today())})
        save_queue(queue)
        commit_queue(queue)

        post_to_instagram(image_url, ig_caption)
        print("Done!")
        return

if __name__ == "__main__":
    main()
