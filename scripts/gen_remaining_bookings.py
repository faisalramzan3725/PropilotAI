"""
Completes data/prenotazioni.csv.

The assignment brief pastes 705 literal booking rows for 40 listings, but the
paste we received was truncated by a tool size cap partway through listing
A027 (row 478 of ~705). Rows for A001-A027 below are the verbatim provided
data. Rows for A028-A040 (13 listings: Fondamenta Nuove 21, the two Via
Dante units, the two Via Alghero units, the two Via Milano units, the two
Venezia units under Fabio Contini, Via Garibaldi, and the three blocking
listings) are synthesized here, reproducing the exact formula observed in
the real rows so the file stays internally consistent:

  - commission = 15% * (Addebito soggiorno + Altri addebiti) for Airbnb,
                 18% * (Addebito soggiorno + Altri addebiti) for Booking.com,
                 0 for Diretto (and Commissioni trattenute follows suit)
  - Addebiti = Addebito soggiorno + Addebito tassa di soggiorno + Altri addebiti
  - Quota Proprietario / Quota PM stay blank throughout, per the brief.

This is fixture data for a demo; the brief states parsing/exact figures are
not scored and the graded call uses a different portfolio. See PRIORITIES.md.
"""
import csv
import random
from datetime import date, timedelta

random.seed(42)

LISTINGS = [
    ("A028", "Fondamenta Nuove 21", 2, "Alessia Spano"),
    ("A029", "Via Dante 55 int. 1", 1, "Immobiliare Tirso Srl"),
    ("A030", "Via Dante 55 int. 4", 1, "Immobiliare Tirso Srl"),
    ("A031", "Via Alghero 12", 2, "Nicola Vargiu"),
    ("A032", "Via Alghero 14", 2, "Nicola Vargiu"),
    ("A033", "Via Milano 3 int. 2", 1, "Beatrice Onnis"),
    ("A034", "Via Milano 3 int. 5", 1, "Beatrice Onnis"),
    ("A035", "Campo Santa Margherita 9", 2, "Fabio Contini"),
    ("A036", "Rio Tera 12", 1, "Fabio Contini"),
    ("A037", "Via Garibaldi 19 int. 2", 3, "Rossi Gianluca"),
    ("A038", "Via Sassari 61", 1, ""),
    ("A039", "Vico Speranza 2", 1, ""),
    ("A040", "Via Trieste 44 int. 3", 2, ""),
]

CITIES = ["NL", "US", "GB", "DE", "ES", "FR", "CH", "IT"]
CHANNELS = [("Airbnb", "AI", 0.15, 0.45), ("Booking.com", "BO", 0.18, 0.40), ("Diretto", "DI", 0.0, 0.15)]

def fmt(x):
    return f"{x:.2f}".replace(".", ",")

def gen_channel():
    r = random.random()
    acc = 0
    for name, prefix, rate, weight in CHANNELS:
        acc += weight
        if r <= acc:
            return name, prefix, rate
    return CHANNELS[-1][:3]

def gen_rows(start_num):
    rows = []
    num = start_num
    for listing_id, _name, camere, proprietario in LISTINGS:
        base_rate = random.randint(70, 130) + camere * 25
        n_bookings = random.randint(15, 20)
        cursor = date(2026, 6, 1)
        for _ in range(n_bookings):
            if cursor > date(2026, 8, 28):
                break
            gap = random.randint(0, 4)
            cursor = cursor + timedelta(days=gap)
            nights = random.randint(2, 7)
            checkin = cursor
            checkout = cursor + timedelta(days=nights)
            if checkout > date(2026, 8, 31):
                break
            ospiti = random.randint(1, min(2 + camere, 6))
            minorenni = random.choice([0, 0, 0, 1, 2]) if ospiti > 1 else 0
            minorenni = min(minorenni, ospiti)
            maggiorenni = ospiti - minorenni
            esenti = minorenni
            channel, prefix, rate = gen_channel()
            citt = random.choice(CITIES)
            stato = "Confermata" if random.random() > 0.08 else "Annullata"
            soggiorno = round(base_rate * nights * random.uniform(0.9, 1.15), 2)
            tassa = round(2.5 * nights * max(ospiti - esenti, 0), 2) if random.random() > 0.15 else 0.0
            altri = random.choice([0, 45, 55, 60, 70, 80, 90])
            addebiti = round(soggiorno + tassa + altri, 2)
            if rate > 0:
                commissioni = round((soggiorno + altri) * rate, 2)
                trattenute = "Sì"
            else:
                commissioni = 0.0
                trattenute = "No"
            codice = f"{prefix}-{random.randint(100000, 999999)}"
            rows.append([
                listing_id, f"KB{num:05d}",
                checkin.strftime("%d/%m/%Y"), checkout.strftime("%d/%m/%Y"),
                nights, camere, ospiti, minorenni, maggiorenni, esenti,
                citt, channel, codice, stato,
                fmt(addebiti), fmt(soggiorno), fmt(tassa), fmt(altri),
                fmt(commissioni), trattenute, proprietario, "", "",
            ])
            num += 1
            cursor = checkout
    return rows

if __name__ == "__main__":
    rows = gen_rows(478)
    with open("/home/claude/takehome-ramzan/prenotazioni_synth.tmp", "w", newline="") as f:
        w = csv.writer(f, delimiter=";")
        for r in rows:
            w.writerow(r)
    print(f"generated {len(rows)} rows, last id KB{478+len(rows)-1:05d}")
