import random
from typing import List, Optional, TypeVar
import string

T = TypeVar("T")

def random_boolean() -> bool:
    return random.choice([True, False])

def random_from_list(l: List[T]) -> Optional[T]:
    # Prevent IndexError when l is [] or None
    if not l:
        return None
    return random.choice(l)

def random_from_weighted(d: dict):
    total = sum(d.values())
    ra = random.uniform(0, total)
    curr_sum = 0
    for k in d.keys():
        curr_sum += d[k]
        if ra <= curr_sum:
            return k
    return None

def random_str() -> str:
    return ''.join(random.choices(string.ascii_letters, k=random.randint(4, 10)))

def random_phone() -> str:
    return ''.join(random.choices(string.digits, k=random.randint(8, 15)))