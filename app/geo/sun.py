"""Положение солнца: азимут заката, белые ночи.

Формулы — классические (склонение по дню года, поправка на рефракцию), точность
порядка 0,5° по азимуту — достаточно, чтобы понять, в какую сторону окна видят закат.
"""
from __future__ import annotations

import math
from datetime import date

MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_RU_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def declination(d: date) -> float:
    """Склонение солнца, градусы."""
    n = d.timetuple().tm_yday
    return -23.44 * math.cos(math.radians(360.0 / 365.0 * (n + 10)))


def sun_azimuth_at_altitude(lat: float, d: date, alt_deg: float, setting: bool = True) -> float | None:
    """Азимут солнца (по часовой от севера), когда оно на высоте alt_deg над горизонтом.

    setting=True — западная половина неба (закат). None — солнце не опускается/не поднимается до этой высоты.
    """
    dec = math.radians(declination(d))
    phi = math.radians(lat)
    alt = math.radians(alt_deg)
    c = (math.sin(dec) - math.sin(alt) * math.sin(phi)) / (math.cos(alt) * math.cos(phi))
    if c < -1.0 or c > 1.0:
        return None
    az = math.degrees(math.acos(c))  # от севера к востоку, для восхода
    return (360.0 - az) if setting else az


def min_altitude(lat: float, d: date) -> float:
    """Высота солнца в нижней кульминации (в полночь по солнечному времени), градусы."""
    return lat + declination(d) - 90.0 if lat >= 0 else -lat - declination(d) - 90.0


def white_night_period(lat: float, depression_deg: float = 9.0, year: int = 2026) -> dict | None:
    """Дни года, когда солнце не опускается глубже depression_deg под горизонт."""
    days = []
    for n in range(1, 366):
        try:
            d = date.fromordinal(date(year, 1, 1).toordinal() + n - 1)
        except ValueError:
            continue
        if min_altitude(lat, d) >= -depression_deg:
            days.append(d)
    if not days:
        return None
    return {
        "start": f"{days[0].day} {MONTHS_RU_GEN[days[0].month - 1]}",
        "end": f"{days[-1].day} {MONTHS_RU_GEN[days[-1].month - 1]}",
        "days": len(days),
        "months": sorted({d.month for d in days}),
        "depression_deg": depression_deg,
    }


def month_reps(year: int = 2026) -> list[date]:
    return [date(year, m, 15) for m in range(1, 13)]
