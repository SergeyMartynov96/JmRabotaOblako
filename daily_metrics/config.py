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

# Exact counterparties selected in the invoice report, shared by all three stores.
# Separate Gazprom/holiday/dessert counterparties are not silently included.
INVOICE_SUPPLIERS = {
    3242:'ИП Шабалда Роман Николаевич',
    3544:'Индивидуальный предприниматель Веселов Александр Павлович',
    4086:'ИП Нигаматуллин Денис Эдуардович',
    5329:'ИП Сиразетдинов Артур Феликсович',
    5361:'Амбруази',
    5515:'ИП Яровиков Максим Дмитриевич',
    5918:'ИП МАЗРОВА ОЛЬГА ВЛАДИМИРОВНА',
    8334:'ИП Курмачев Михаил Иванович',
    9388:'ИП Веденева Вера Михайловна',
    10736:'ИП Парамонова Н.В.',
    11164:'ИП СЕРГЕЕВА ЮЛИЯ ДМИТРИЕВНА',
    11519:'КАБО',11873:'КИСЕЛЬ',12007:'Мисфуд',
    12935:'ИП Мурачев Михаил Анатольевич',
    13166:'РАЗОГРЕЛИ И ПОЕЛИ',13442:'КАЧЕСТВО ВКУСА',
    13456:'ИП Водопьянова Марина Михайловна',13510:'Маркетинг и еда',
    13743:'Индивидуальный Предприниматель Мамадалиев Мухаммадали Арипович',
    14172:'ИП Маматулин Данис Маратович',14219:'ЗАВОД ЕКБ',
    15724:'ИП Сафарова Амалия Вячеславовна',
}
