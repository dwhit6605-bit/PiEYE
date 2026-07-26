import cv2
import requests


class NtfyNotifier:
    """Push to an ntfy topic. If a frame is given it's attached as a JPEG snapshot."""

    def __init__(self, server="https://ntfy.sh", topic="change-me", priority="high",
                 token=None):
        self.url = f"{server.rstrip('/')}/{topic}"
        self.priority = str(priority)
        self.token = token

    def send(self, title, message, frame=None, tags=None):
        headers = {"Title": title, "Priority": self.priority}
        if tags:
            headers["Tags"] = ",".join(tags)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        if frame is not None:
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                headers["Filename"] = "snapshot.jpg"
                headers["Message"] = message  # body is the image, so text goes in a header
                data = buf.tobytes()
            else:
                data = message.encode("utf-8")
        else:
            data = message.encode("utf-8")

        try:
            requests.put(self.url, data=data, headers=headers, timeout=15)
        except requests.RequestException as e:
            print(f"[notify] failed to send: {e}", flush=True)
