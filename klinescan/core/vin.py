"""VIN decoding: World Manufacturer Identifier, model year and check digit.

Sources
-------
* Model-year codes (position 10), the position-7 cycle rule for North American
  passenger cars/MPVs and the check-digit algorithm (position 9):
  https://en.wikipedia.org/wiki/Vehicle_identification_number
  (worked example: ``1M8GDM9A_KP042788`` -> sum 351 -> 351 mod 11 = 10 -> ``X``).
* WMI codes: the Wikipedia article above and the Wikibooks WMI list,
  https://en.wikibooks.org/wiki/Vehicle_Identification_Numbers_(VIN_codes)/World_Manufacturer_Identifier_(WMI).
  WMIs identify the *manufacturer*, not always the brand (e.g. KNM = Renault
  Samsung, also building Nissans), so results are reported as "VIN suggests".

The check digit is mandatory in North America (and China); European VINs often
do not use it, so a mismatch is only flagged, never treated as an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

_TRANSLIT = {**{str(d): d for d in range(10)},
             "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
             "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
             "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9}
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_YEAR_LETTERS = "ABCDEFGHJKLMNPRSTVWXY"  # I, O, Q, U, Z and 0 are not used


def model_year_candidates(code: str) -> List[int]:
    """All model years a position-10 code can mean (30-year cycle)."""
    if code in _YEAR_LETTERS:
        base = 1980 + _YEAR_LETTERS.index(code)
        return [base, base + 30]
    if code.isdigit() and code != "0":
        base = 2000 + int(code)
        return [base, base + 30]
    return []


def check_digit(vin: str) -> Optional[str]:
    if len(vin) != 17:
        return None
    try:
        total = sum(_TRANSLIT[c] * w for c, w in zip(vin.upper(), _WEIGHTS))
    except KeyError:
        return None
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


# Manufacturer -> brand family used to match the decoded catalogue.
# Three-character codes first, then two-character prefixes.
WMI = {
    # Toyota group
    **{c: "TOYOTA" for c in ("JT1", "JT2", "JT3", "JT4", "JT5", "JT7", "JTB", "JTD", "JTE", "JTF", "JTG",
                             "JTK", "JTL", "JTM", "JTN", "JTP", "AHT", "SB1", "TW1", "YAR", "XW7", "RL4",
                             "MR0", "MR1", "MR2", "MR3", "MBJ", "MHF", "RKL", "PN1", "PN2", "LFM", "LTV", "LVG")},
    **{c: "LEXUS" for c in ("JTH", "JTJ", "JT6", "JT8", "2T2")},
    **{c: "DAIHATSU" for c in ("JD1", "JD2", "JD4", "JDA", "MHK")},
    **{c: "HONDA" for c in ("JHM", "JHL", "JH1", "JH2", "JH3", "LUC", "LHG", "LVH", "1HG", "2HG", "2HJ",
                            "2HK", "3CZ", "3HG", "19X", "SHH", "SHS", "MRH", "MAK")},
    **{c: "ACURA" for c in ("JH4", "19U", "19V", "2HH", "2HN", "3HD")},
    **{c: "NISSAN" for c in ("JN1", "JN3", "JN6", "JN8", "LGB", "LJN", "ADN", "MDH", "MNT", "PN8", "VSK",
                             "VWA", "VNV", "SJN", "1N4", "1N6", "Z8N")},
    **{c: "INFINITI" for c in ("JNK", "JNR", "JNX", "SJK")},
    **{c: "MITSUBISHI" for c in ("JA3", "JA4", "JA7", "JE3", "JE4", "JMA", "JMB", "JMF", "JMP", "JMR", "JMY",
                                 "LL6", "MK2", "XMC", "XMD", "4A3", "4A4", "4P3")},
    **{c: "MAZDA" for c in ("JM0", "JM1", "JM2", "JM3", "JM4", "JM6", "JM7", "JMZ", "LVR", "PP1", "YCM",
                            "1YV", "3MD", "3MZ")},
    **{c: "SUBARU" for c in ("JF1", "JF2", "JF3", "MMR", "PLP", "4S3", "4S4")},
    **{c: "SUZUKI" for c in ("JS2", "JS3", "JS4", "JSA", "LVF", "MMS", "PPP", "VSE", "2S2", "2S3", "MHY")},
    **{c: "MARUTI" for c in ("MA3",)},
    **{c: "ISUZU" for c in ("JAA", "JAB", "JAC", "JAL", "JAM", "LET", "LWL", "MP1", "MPA", "PAB", "ACV")},
    **{c: "HYUNDAI" for c in ("KMC", "KME", "KMF", "KMH", "KMJ", "KM8", "KMX", "LBE", "AC5", "MAL", "MB2",
                              "MF3", "PL8", "PP3", "NLH", "TMA", "TMC", "Z94")},
    **{c: "GENESIS" for c in ("KMT", "KMU")},
    **{c: "KIA" for c in ("KNA", "KNC", "KND", "KNE", "KNF", "KNG", "LJD", "MZB", "U5Y", "U6Y", "3KP", "XWK")},
    **{c: "SSANGYONG" for c in ("KPA", "KPB", "KPD", "KPT")},
    **{c: "RENAULT SAMSUNG" for c in ("KNM",)},
    **{c: "GM DAEWOO/CHEVROLET" for c in ("KLA", "KLY", "KL1", "KL2", "KL4", "KL6", "KL7", "KL8")},
    **{c: "HOLDEN" for c in ("KL3",)},
    **{c: "MERCEDES BENZ" for c in ("WDB", "WDC", "WDD", "WDF", "WD3", "WD4", "W1K", "W1N", "W1T", "W1V",
                                    "ADB", "LE4", "MBR", "VSA")},
    **{c: "BMW/MINI" for c in ("WBA", "WBS", "WBX", "WBY", "WB1", "WMW", "WMZ", "LBV", "ABM")},
    **{c: "VOLKS WAGEN" for c in ("WVW", "WVG", "WV1", "WV2", "AAV", "LFV", "LSV", "XW8", "YBW")},
    **{c: "AUDI" for c in ("WAU", "WA1", "WUA", "TRU", "AAA")},
    **{c: "SEAT" for c in ("VSS",)},
    **{c: "SKODA" for c in ("TMB",)},
    **{c: "PORSHE" for c in ("WP0", "WP1")},
    **{c: "OPEL" for c in ("W0L", "W0V", "VXK")},
    **{c: "FORD EUROPEAN" for c in ("WF0", "VS6", "SFA", "XLC", "X9F")},
    **{c: "FORD USA" for c in ("1FA", "1FB", "1FC", "1FD", "1FM", "1FT", "2FA", "2FM", "2FT", "3FA", "3FM")},
    **{c: "CHRYSLER" for c in ("1C3", "2C3", "1A4", "1A8", "2A4", "2A8", "3C3", "3C4")},
    **{c: "DODGE" for c in ("JB3", "JB4", "JB7", "LMG", "LMW")},
    **{c: "JEEP" for c in ("1J4", "1J7", "1J8", "ZAC")},
    **{c: "GM" for c in ("1G1", "1GC", "1G4", "1G6", "1GT", "1GK", "3G1", "3GN", "LSG", "LRB", "LRE")},
    **{c: "PEUGEOT" for c in ("VF3", "VR3", "VS8", "LDC")},
    **{c: "CITROEN" for c in ("VF7", "VR7", "VS7")},
    **{c: "RENAULT" for c in ("VF1", "VF2", "VF6", "VNV", "X7L")},
    **{c: "FIAT" for c in ("ZFA", "ZFB", "ZFC", "AFZ")},
    **{c: "ALFA ROMEO" for c in ("ZAR", "ZAS")},
    **{c: "LANCIA" for c in ("ZLA",)},
    **{c: "VOLVO" for c in ("YV1", "YV4", "LVY")},
    **{c: "SAAB" for c in ("YS3",)},
    **{c: "JAGUAR" for c in ("SAJ",)},
    **{c: "ROVER" for c in ("SAL",)},
    **{c: "PROTON" for c in ("PL1",)},
    **{c: "PERODUA" for c in ("PM2",)},
    **{c: "TATA" for c in ("MAT",)},
    **{c: "MAHINDRA" for c in ("MA1",)},
    **{c: "CHERY" for c in ("LVV", "LVU", "LVT", "LNN")},
    **{c: "BYD" for c in ("LC0", "LGX", "LPE")},
    **{c: "GREAT WALL" for c in ("LGW",)},
    **{c: "GEELY" for c in ("LB3",)},
    **{c: "LADA" for c in ("XTA",)},
}
WMI_PREFIX2 = {"KM": "HYUNDAI", "KL": "GM DAEWOO/CHEVROLET", "1G": "GM", "2G": "GM", "3G": "GM"}

# Brand family -> common catalogue brand names.
CATALOG_BRANDS = {
    "HYUNDAI": ("HYUNDAI USA", "HYUNDAI GENERAL", "HYUNDAI"),
    "KIA": ("KIA USA", "KIA GENERAL", "KIA"),
    "GENESIS": ("HYUNDAI USA", "HYUNDAI GENERAL"),
    "HOLDEN": ("HOLDEN DOMESTIC", "HOLDEN EXPORT"),
    "FORD USA": ("FORD USA",), "FORD EUROPEAN": ("FORD EUROPEAN",), "FORD AUSTRALIA": ("FORD AUSTRALIA",),
    "MARUTI": ("MARUTI", "SUZUKI"),
}


@dataclass
class VinInfo:
    vin: str
    valid_format: bool
    wmi: str = ""
    manufacturer: str = ""
    model_years: List[int] = field(default_factory=list)
    check_digit_ok: Optional[bool] = None
    notes: List[str] = field(default_factory=list)

    @property
    def likely_year(self) -> Optional[int]:
        """Most recent candidate year that is not in the future (the 30-year cycle repeats codes)."""
        from datetime import date

        plausible = [y for y in self.model_years if y <= date.today().year + 1]
        return max(plausible) if plausible else None

    @property
    def catalog_brands(self) -> tuple:
        if not self.manufacturer:
            return ()
        return CATALOG_BRANDS.get(self.manufacturer, (self.manufacturer,))


def decode_vin(vin: str) -> VinInfo:
    vin = (vin or "").strip().upper().replace(" ", "")
    info = VinInfo(vin=vin, valid_format=bool(VIN_RE.match(vin)))
    if not info.valid_format:
        info.notes.append("not a 17-character VIN (I, O and Q are never used)")
        return info
    info.wmi = vin[:3]
    info.manufacturer = WMI.get(info.wmi) or WMI_PREFIX2.get(vin[:2], "")
    years = model_year_candidates(vin[9])
    north_america = vin[0] in "12345"
    if north_america and years and vin[6]:
        # Position 7 numeric -> 1980-2009, alphabetic -> 2010-2039 (NA passenger cars/MPVs)
        years = [y for y in years if (y < 2010) == vin[6].isdigit()] or years
    info.model_years = years
    expected = check_digit(vin)
    info.check_digit_ok = expected == vin[8]
    if not info.check_digit_ok:
        info.notes.append("check digit does not match (expected outside North America/China)")
    if not info.manufacturer:
        info.notes.append(f"WMI {info.wmi} not in the built-in table")
    return info


VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"


def vpic_lookup(vin: str, timeout: float = 8.0) -> dict:
    """Opt-in online decode via NHTSA vPIC (free, no key).

    This SENDS THE VIN to a US government web service, so callers must only use
    it after the user explicitly agrees.  Endpoint and fields (Make, Model,
    ModelYear, Trim, ...) per https://vpic.nhtsa.dot.gov/api/ ; the response is
    ``{"Results": [ {...} ]}`` with one object.  Coverage is best for vehicles
    sold in North America.
    """
    import json
    import urllib.request

    info = decode_vin(vin)
    if not info.valid_format:
        raise ValueError("not a valid 17-character VIN")
    request = urllib.request.Request(VPIC_URL.format(vin=info.vin), headers={"User-Agent": "KLineScan"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        results = json.loads(response.read().decode("utf-8")).get("Results") or [{}]
    data = results[0]
    keys = ("Make", "Model", "ModelYear", "Trim", "Series", "BodyClass", "EngineModel",
            "DisplacementL", "FuelTypePrimary", "PlantCountry", "ErrorText")
    return {k: data.get(k, "") for k in keys if data.get(k)}
