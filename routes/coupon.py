from flask import Blueprint, render_template
import requests
from bs4 import BeautifulSoup
from datetime import date, timedelta, datetime

bp_coupon = Blueprint("coupon", __name__)

@bp_coupon.route("/coupon")
def coupon():
    url = "https://www.italotreno.com/it/promo-week"
    r = requests.get(url, timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")

    # estrai nome coupon dall'immagine promo
    img = soup.select_one(".img-container img")
    coupon_name = None
    if img and img.has_attr("alt"):
        coupon_name = img["alt"].split("_")[0]  # es: "promowe"

    # estrai scadenza (strong con "Acquista entro...")
    expiry = None
    strongs = soup.select(".condizioni-box strong")
    for s in strongs:
        if "Acquista entro" in s.text:
            expiry = s.text
            break

    # data scadenza come oggetto datetime
    expiry_date = None
    if expiry:
        try:
            # es: "Acquista entro le ore 18.00 del 22.09.2025."
            parts = expiry.split("del")[-1].strip().strip(".")
            expiry_date = datetime.strptime(parts, "%d.%m.%Y").date()
        except Exception:
            pass

    # genera martedì fino a expiry_date
    tuesdays = []
    today = date.today()
    d = today
    while expiry_date and d <= expiry_date:
        if d.weekday() == 1:  # 0=lunedì, 1=martedì
            tuesdays.append(d)
        d += timedelta(days=1)

    return render_template(
        "coupon.html",
        coupon_name=coupon_name,
        expiry=expiry,
        tuesdays=tuesdays
    )
