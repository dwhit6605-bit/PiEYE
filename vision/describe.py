import base64

import cv2


class ClaudeDescriber:
    """OPTIONAL cloud tier. Turns a snapshot into a one-line description.

    Reads the API key from the ANTHROPIC_API_KEY environment variable. Only
    constructed when `claude.enabled: true` in the config, so the rest of the
    system runs fully offline without the `anthropic` package installed.
    """

    def __init__(self, model="claude-haiku-4-5-20251001", max_tokens=150):
        import anthropic  # lazy import; only needed when the cloud tier is on
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def describe(self, frame, hint=""):
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        b64 = base64.standard_b64encode(buf.tobytes()).decode("ascii")
        prompt = (
            "This is a still frame from a home security camera. "
            f"{hint} In one or two short sentences, say who or what is present "
            "and what they appear to be doing. If nothing is notable, reply "
            "exactly 'nothing notable'."
        )
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return msg.content[0].text.strip()
