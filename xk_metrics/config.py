from dataclasses import dataclass
from daily_metrics.config import Branch
BOOK='17RaOtwXEzD6X6nhHgRRlCj1xKaasKqZA7V22R-V8WC0'

@dataclass(frozen=True)
class Store:
    branch: Branch
    offset: int
    marker: str
    tops_gid: int

STORES={
    'Х76':Store(Branch('Х76',401,'ЖМ Тюмень Харьковская 76',BOOK,0,580030514,'1sr8Frrpbgzd4PxSaIfXEzL5QLvJcI_sL9XRSRhUVwyc'),0,'х76',606438044),
    'К44':Store(Branch('К44',140,'Миниформат / ЕКБ Атриум, Куйбышева, 44',BOOK,0,293705136,'1MESnu4zrjXkBkUGHNu47RSOLDNStsAc0Wsy_pQZBnb0'),25,'атриум',1681074484),
}
