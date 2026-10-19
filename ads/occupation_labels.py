"""
English names for JobTech occupation groups.

JobTech only sends Swedish labels ("Mjukvaru- och systemutvecklare m.fl.").
The database keeps exactly what JobTech sent (the source of truth); the API
translates on the way out, looked up by the group's CODE — codes are stable,
label spellings are not (JobTech sometimes sends no label at all).

The names follow SSYK 2012, Sweden's occupation classification (SCB publishes
it in English too). A group not in this table yet — a new import may bring
one — is shown with its Swedish label instead of breaking: add it here.
"""

OCCUPATION_GROUPS_EN = {
    "DJh5_yyF_hEM": "Software and systems developers",           # Mjukvaru- och systemutvecklare m.fl.
    "D9SL_mtn_vGM": "Software testers and test managers",        # Systemtestare och testledare
    "hmaC_cfi_UKg": "IT support technicians",                    # Supporttekniker, IT
    "UXKZ_3zZ_ipB": "Systems analysts and IT architects",        # Systemanalytiker och IT-arkitekter m.fl.
    "13md_uyV_BNG": "IT operations technicians",                 # Drifttekniker, IT
    "MYAz_x9m_2LJ": "System administrators",                     # Systemadministratörer
    "VCpu_5EN_bBt": "Network and systems technicians",           # Nätverks- och systemtekniker m.fl.
    "BAeH_eg8_T2d": "IT security specialists",                   # IT-säkerhetsspecialister
    "cBBa_ngH_fCx": "IT system managers",                        # Systemförvaltare m.fl.
    "UxT1_tPF_Kbg": "Other IT specialists",                      # Övriga IT-specialister
    "Q5DF_juj_8do": "Game and digital media developers",         # Utvecklare inom spel och digitala media
    "dUuh_wwa_FGj": "Other agents and brokers",                  # Övriga förmedlare
    "fWrt_sLg_koW": "Electronics repairers and communication electricians",  # Elektronikreparatörer och kommunikationselektriker m.fl.
    "nuXg_Wjc_JPd": "Safety inspectors",                         # Säkerhetsinspektörer m.fl.
}


def english_label(code, swedish_label=""):
    """The English name of a group; its Swedish label if there's none yet."""
    return OCCUPATION_GROUPS_EN.get(code, swedish_label)
