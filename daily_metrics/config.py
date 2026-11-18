from dataclasses import dataclass

@dataclass(frozen=True)
class Branch:
    name: str
    department: int
    title: str
    book: str
    revenue_gid: int
    procurement_gid: int
    tops_book: str
    newest_first: bool = False

BRANCHES = (
    Branch('АВ12', 115, 'ЖМ ЕКБ Антона Валека, 12',
           '10oYGVjhn5AEEz62CTDbYWRtfLRPk715oh9jT6CPoH5Y', 766772597, 672003479,
           '1Tpoj04D7tshbPsJNNMuCqLfRNplotX7LMTCD3IMJQOs'),
    Branch('М42', 153, 'ЖМ ЕКБ Малышева, 42',
           '1bjWG6XBZRmOTd-UF2v7MlXKN-cXPJNAJ9rWzqu6gQQw', 0, 1613797398,
           '1luhVABANqTP2HrcK7rjbJuqTBZd0yANu9kOzN0dvH5I', True),
    Branch('Г34', 357, 'ЖМ Тюмень Газовиков 34',
           '1Ju0nexwNF3KQdYUBjZ2OSzNBQbtZIl52KJFMv6VbB-g', 1644140493, 708404997,
           '12td6TPQhsOsegoXO14wwy7EaQKryCafk0waEaBsHK0Q'),
)
