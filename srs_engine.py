from datetime import datetime, timedelta
from enum import IntEnum


class ReviewQuality(IntEnum):
    FORGOT = 0
    HARD = 1
    GOOD = 2
    EASY = 3


def calculate_next_review(interval, ease_factor, repetitions, quality):
    """
    Menghitung jadwal review berikutnya berdasarkan algoritma SM-2.

    Args:
        interval (int): Interval review sebelumnya dalam hari.
        ease_factor (float): Faktor kemudahan kartu (minimum 1.3).
        repetitions (int): Jumlah jawaban berturut-turut dengan kualitas >= GOOD.
        quality (ReviewQuality): Kualitas jawaban pengguna.

    Returns:
        dict: Berisi interval, ease_factor, repetitions, dan next_review_date baru.
    """
    MIN_EASE = 1.3
    MAX_EASE = 3.0

    if quality == ReviewQuality.FORGOT:
        new_repetitions = 0
        new_interval = 1
        new_ease = max(MIN_EASE, ease_factor - 0.2)

    elif quality == ReviewQuality.HARD:
        new_repetitions = repetitions
        new_interval = max(1, int(interval * 1.2))
        new_ease = max(MIN_EASE, ease_factor - 0.15)

    elif quality == ReviewQuality.GOOD:
        new_repetitions = repetitions + 1
        new_ease = ease_factor

        if repetitions == 0:
            new_interval = 1
        elif repetitions == 1:
            new_interval = 6
        else:
            new_interval = int(interval * ease_factor)

    elif quality == ReviewQuality.EASY:
        new_repetitions = repetitions + 1
        new_interval = int(interval * ease_factor * 1.3)
        new_ease = min(MAX_EASE, ease_factor + 0.15)

    next_review_date = datetime.now() + timedelta(days=new_interval)

    return {
        "interval": new_interval,
        "ease_factor": round(new_ease, 2),
        "repetitions": new_repetitions,
        "next_review_date": next_review_date,
    }