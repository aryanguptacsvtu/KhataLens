"""Reminder text + click-to-chat WhatsApp links. No WhatsApp API, no cost, no data sent anywhere:
the link just opens WhatsApp on the shopkeeper's own phone/laptop with the message pre-filled."""
from __future__ import annotations

import re
from urllib.parse import quote

TEMPLATES = {
    "hi": "नमस्ते {name} जी, {shop} में आपका ₹{amount:,.0f} बाकी है। जब सुविधा हो, कृपया जमा कर दें। धन्यवाद ",
    "mr": "नमस्कार {name}, {shop} मध्ये तुमचे ₹{amount:,.0f} बाकी आहेत. सोयीनुसार जमा करा. धन्यवाद ",
    "ta": "வணக்கம் {name}, {shop} கடையில் உங்கள் ₹{amount:,.0f} நிலுவையில் உள்ளது. வசதிப்படும்போது செலுத்தவும். நன்றி ",
    "en": "Hello {name}, a gentle reminder that ₹{amount:,.0f} is pending at {shop}. Please pay when convenient. Thank you ",
}


def reminder_text(name: str, amount: float, shop: str = "हमारी दुकान", lang: str = "hi") -> str:
    tpl = TEMPLATES.get(lang, TEMPLATES["hi"])
    return tpl.format(name=name, amount=amount, shop=shop)


def clean_phone(phone: str, default_country: str = "91") -> str | None:
    """Return digits-only international number for wa.me, or None if unusable."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        digits = default_country + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = default_country + digits[1:]
    return digits if 11 <= len(digits) <= 15 else None


def whatsapp_link(phone: str, message: str) -> str | None:
    number = clean_phone(phone)
    if not number:
        return None
    return f"https://wa.me/{number}?text={quote(message)}"
