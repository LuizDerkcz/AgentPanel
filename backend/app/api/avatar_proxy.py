from fastapi import APIRouter, Query, Response
import requests
import os

router = APIRouter()

AVATAR_DIR = os.path.join(os.path.dirname(__file__), "../../avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)


@router.get("/avatar")
def proxy_avatar(url: str):
    try:
        # 下载图片并保存到本地
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            ext = r.headers.get("Content-Type", "image/png").split("/")[-1]
            filename = os.path.basename(url).split("?")[0]
            local_path = os.path.join(AVATAR_DIR, filename)
            with open(local_path, "wb") as f:
                f.write(r.content)
            return {"local_path": f"/avatars/{filename}"}
        return Response(status_code=404)
    except Exception:
        return Response(status_code=500)


@router.get("/avatars/{filename}")
def get_avatar(filename: str):
    local_path = os.path.join(AVATAR_DIR, filename)
    if os.path.exists(local_path):
        with open(local_path, "rb") as f:
            content = f.read()
        ext = filename.split(".")[-1]
        media_type = f"image/{ext if ext != 'jpg' else 'jpeg'}"
        return Response(content=content, media_type=media_type)
    return Response(status_code=404)
