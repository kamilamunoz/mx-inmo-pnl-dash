"""Motor de agregación P&L Inmobiliaria México por (mes de facturación, region).

Estructura basada en el dashboard interno "Corporate_Finance_Master_Dashboard"
sección Unit Inmobiliaria — hoja "P&L INMOBILIARIA MÉXICO", validada 2026-07-29
contra jun-2026 (23 Inmo 100 + 6 Tradicional, GMV $31.3M MXN).

Convenciones específicas Inmo (distintas al MM):
- Fecha canónica: `c_fecha_factura_netsuite` (DATE).
- Producto Inmo 100 vs Inmo Tradicional: `linea_negocio_hc100` ('Si' / 'No').
  El campo `tipo_de_inmobiliaria` NO es determinante — puede tener HC100 mezclado.
- Fee HC100 (Inmo) = `c_precio - c_precio_sin_inmo100`. Nombre de columna es "inmo100"
  pero es equivalente conceptual del fee HC100 del MM.
- GMV base para units = `c_precio` (con fee incluido). Kamila validó 2026-07-29
  que los units se miden sobre el GMV con fee (misma regla que MM).
- Base P&L "Inmo" = solo NIDs con hc100 IN ('Si', 'No'). NULLs se excluyen del universo.
- NO hay Purchase Price, Remodeling ni Holding — Inmo no toma inventario propio.

Vistas:
- ACC       → usa columnas *_accounting.
- Sintético → usa *_ue con fallback a *_accounting fila por fila (donde aplique).

Todos los valores en MXN.
"""

from __future__ import annotations

import pandas as pd

# Umbral de filas totales para colapsar en 'Otros'
MIN_ROWS_PER_REGION = 30
# 46% de los NIDs Inmo MX tienen region=NULL. Los rescatamos por ciudad + area_metropolitana.
DEFAULT_REGION_FOR_NULLS = "Sin región"
LABEL_OTROS = "Otros"
# GUANAJUATO e HIDALGO se ven poco pero se muestran individuales (paridad con MM MX).
WHITELIST_REGIONS: set[str] = {"GUANAJUATO", "HIDALGO"}

# ─────────────────────────────────────────────────────────────────────────────
# Mapping ciudad→region + area_metropolitana→region (fallback) para rescatar
# NIDs con region=NULL. Ground truth construido 2026-07-30 desde los NIDs Inmo
# que sí tienen region etiquetada + geografía manual para las ciudades faltantes.
# ─────────────────────────────────────────────────────────────────────────────

CITY_TO_REGION: dict[str, str] = {
    # CDMX
    "Ciudad de México": "CDMX",
    "Chapultepec": "CDMX",
    # EDO MEX
    "Tecámac": "EDO MEX", "Huehuetoca": "EDO MEX", "Zumpango": "EDO MEX",
    "Ecatepec de Morelos": "EDO MEX", "Chalco": "EDO MEX", "Toluca": "EDO MEX",
    "Ixtapaluca": "EDO MEX", "Chicoloapan": "EDO MEX", "Acolman": "EDO MEX",
    "Cuautitlan Izcalli": "EDO MEX", "Cuautitlan": "EDO MEX", "Tultepec": "EDO MEX",
    "Zempoala": "EDO MEX", "Nextlalpan": "EDO MEX", "Tlalnepantla de Baz": "EDO MEX",
    "Melchor Ocampo": "EDO MEX", "Teoloyucan": "EDO MEX", "San Mateo Atenco": "EDO MEX",
    "Temoaya": "EDO MEX", "Huixquilucan": "EDO MEX", "Valle de Chalco Solidaridad": "EDO MEX",
    "Metepec": "EDO MEX", "Zinacantepec": "EDO MEX", "Coacalco de Berriozábal": "EDO MEX",
    "Tultitlán": "EDO MEX", "Nicolás Romero": "EDO MEX", "Almoloya de Juárez": "EDO MEX",
    "Nezahualcóyotl": "EDO MEX", "Pachuca de Soto": "EDO MEX",  # data usa EDO MEX no HIDALGO
    "Tizayuca": "EDO MEX",  # data usa EDO MEX aunque geo sea HIDALGO
    # HIDALGO (única ciudad etiquetada consistentemente)
    "Mineral de la Reforma": "HIDALGO",
    # JALISCO
    "Tlajomulco de Zúñiga": "JALISCO", "Tonalá": "JALISCO",
    "Ixtlahuacán de los Membrillos": "JALISCO", "Zapopan": "JALISCO",
    "San Pedro Tlaquepaque": "JALISCO", "El Salto": "JALISCO", "Guadalajara": "JALISCO",
    "San Antonio la Isla": "JALISCO",  # data usa JALISCO aunque geo sea EDO MEX
    # NUEVO LEON
    "Juárez": "NUEVO LEON", "García": "NUEVO LEON", "Apodaca": "NUEVO LEON",
    "El Carmen": "NUEVO LEON", "Guadalupe": "NUEVO LEON", "General Escobedo": "NUEVO LEON",
    "Salinas Victoria": "NUEVO LEON", "Santa Catarina": "NUEVO LEON",
    "General Zuazua": "NUEVO LEON", "Monterrey": "NUEVO LEON",
    "San Nicolás de los Garza": "NUEVO LEON",
    # QUERETARO
    "Querétaro": "QUERETARO", "El Marqués": "QUERETARO", "Corregidora": "QUERETARO",
    # GUANAJUATO
    "León": "GUANAJUATO",
}

# Fallback si la ciudad no está en el diccionario: usar area_metropolitana.
# "Valle de México" es ambigua (CDMX o EDO MEX) — solo se aplica si ciudad tampoco
# la resuelve. Default a EDO MEX (mayoría histórica).
AREA_METRO_TO_REGION: dict[str, str] = {
    "Guadalajara": "JALISCO",
    "Guanajuato": "GUANAJUATO",
    "Monterrey": "NUEVO LEON",
    "Queretaro": "QUERETARO",
    "Valle de México": "EDO MEX",
    "Hidalgo": "EDO MEX",  # se sobrescribe con ciudad "Mineral de la Reforma" → HIDALGO
}

# Producto split — flag canónico validado contra dashboard interno
PRODUCTO_INMO100 = "Si"
PRODUCTO_TRAD = "No"


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _num(series: pd.Series) -> pd.Series:
    """Convierte a float y trata NaN como 0 para sumas."""
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def _coalesce_ue_acc(df: pd.DataFrame, ue_col: str, acc_col: str) -> pd.Series:
    """Vista Sintético: usa _ue si no es NaN, si no _accounting. Fila por fila."""
    ue = pd.to_numeric(df[ue_col], errors="coerce")
    acc = pd.to_numeric(df[acc_col], errors="coerce")
    return ue.where(ue.notna(), acc).fillna(0.0)


def _normalize_region(region: pd.Series, counts: pd.Series) -> pd.Series:
    """NaN → 'Sin región'. Regiones con <MIN_ROWS_PER_REGION → 'Otros',
    salvo las que estén en WHITELIST_REGIONS.
    """
    below = [r for r in counts[counts < MIN_ROWS_PER_REGION].index.tolist()
             if r not in WHITELIST_REGIONS]
    out = region.where(region.notna(), DEFAULT_REGION_FOR_NULLS)
    out = out.where(~out.isin(below), LABEL_OTROS)
    return out


def _mask_inmo100(df: pd.DataFrame) -> pd.Series:
    return df["linea_negocio_hc100"] == PRODUCTO_INMO100


def _mask_trad(df: pd.DataFrame) -> pd.Series:
    return df["linea_negocio_hc100"] == PRODUCTO_TRAD


# ─────────────────────────────────────────────────────────────────────────────
# preparación
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_region(row) -> str | None:
    """Rescata region desde ciudad + area_metropolitana cuando region es NULL.
    Prioridad: region > ciudad (mapping) > area_metropolitana (fallback).
    """
    r = row.get("region")
    if pd.notna(r):
        return r
    c = row.get("ciudad")
    if pd.notna(c) and c in CITY_TO_REGION:
        return CITY_TO_REGION[c]
    am = row.get("area_metropolitana")
    if pd.notna(am) and am in AREA_METRO_TO_REGION:
        return AREA_METRO_TO_REGION[am]
    return None


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Añade columna `mes` (YYYY-MM string) y `region_norm`.

    Excluye filas con `c_fecha_factura_netsuite` nula. El fetch_raw ya filtra
    hc100 IN ('Si', 'No'), así que aquí no filtramos por producto.

    Los NIDs con region=NULL se rescatan por ciudad + area_metropolitana antes
    de aplicar el umbral MIN_ROWS_PER_REGION.
    """
    out = df.copy()
    fecha = pd.to_datetime(out["c_fecha_factura_netsuite"])
    out = out.loc[fecha.notna()].copy()
    out["mes"] = pd.to_datetime(out["c_fecha_factura_netsuite"]).dt.to_period("M").astype(str)
    out["region_resolved"] = out.apply(_resolve_region, axis=1)
    counts_by_region = out["region_resolved"].value_counts(dropna=False)
    out["region_norm"] = _normalize_region(out["region_resolved"], counts_by_region)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# estructura declarativa del P&L Inmo (basada en el PDF)
# ─────────────────────────────────────────────────────────────────────────────

PNL_STRUCTURE = [
    # ── properties (conteos) ──
    {"key": "properties_inmo100", "label": "Properties Inmo 100", "parent": None, "type": "kpi", "sign": "count"},
    {"key": "properties_trad", "label": "Properties Inmo Tradicional", "parent": None, "type": "kpi", "sign": "count"},
    {"key": "properties_total", "label": "Properties Total", "parent": None, "type": "total", "sign": "count"},

    # ── GMV ──
    {"key": "gmv_inmo100", "label": "GMV Inmo 100", "parent": None, "type": "kpi", "sign": "income"},
    {"key": "gmv_trad", "label": "GMV Inmo Tradicional", "parent": None, "type": "kpi", "sign": "income"},
    {"key": "gmv_total", "label": "(=) GMV Inmobiliaria", "parent": None, "type": "total", "sign": "income"},
    {"key": "avg_ticket", "label": "Avg. Ticket", "parent": None, "type": "kpi", "sign": "ticket"},
    {"key": "fee_hc100", "label": "del cual: Fee HC100", "parent": "gmv_total", "type": "subcuenta", "sign": "net"},

    # ── Gross Profit (comisión cobrada al cliente) ──
    {"key": "gp_inmo100", "label": "Gross Profit Inmo 100", "parent": None, "type": "kpi", "sign": "income"},
    {"key": "gp_trad", "label": "Gross Profit Inmo Tradicional", "parent": None, "type": "kpi", "sign": "income"},
    {"key": "gross_profit", "label": "(=) Gross Profit Total", "parent": None, "type": "total", "sign": "net"},
    {"key": "avg_commission", "label": "Avg. Commission", "parent": None, "type": "kpi", "sign": "ticket"},
    {"key": "pct_fee_charged", "label": "% fee charged", "parent": None, "type": "kpi", "sign": "pct"},

    # ── Brokers externos ──
    {"key": "brokers_inmo100", "label": "Brokers Inmo 100", "parent": "brokers", "type": "subcuenta", "sign": "cost"},
    {"key": "brokers_trad", "label": "Brokers Inmo Tradicional", "parent": "brokers", "type": "subcuenta", "sign": "cost"},
    {"key": "brokers", "label": "Comisión Brokers", "parent": None, "type": "rubro", "sign": "cost"},
    {"key": "pct_fee_paid", "label": "% fee paid", "parent": None, "type": "kpi", "sign": "pct"},

    # ── Gastos transaccionales (4 buckets) ──
    {"key": "tx_avaluo", "label": "Avalúos", "parent": "gastos_transaccionales", "type": "subcuenta", "sign": "cost"},
    {"key": "tx_notariales", "label": "Gastos notariales", "parent": "gastos_transaccionales", "type": "subcuenta", "sign": "cost"},
    {"key": "tx_apertura", "label": "Apertura de expediente", "parent": "gastos_transaccionales", "type": "subcuenta", "sign": "cost"},
    {"key": "tx_inscripcion", "label": "Inscripción de crédito", "parent": "gastos_transaccionales", "type": "subcuenta", "sign": "cost"},
    {"key": "gastos_transaccionales", "label": "Gastos transaccionales", "parent": None, "type": "rubro", "sign": "cost"},

    # ── Comisiones internas · Inmo 100 ──
    {"key": "com_int100_sellers", "label": "Sellers Inmo 100", "parent": "com_int_inmo100", "type": "subcuenta", "sign": "cost"},
    {"key": "com_int100_buyers", "label": "Buyers Inmo 100", "parent": "com_int_inmo100", "type": "subcuenta", "sign": "cost"},
    {"key": "com_int_inmo100", "label": "Comisiones Internas Inmo 100", "parent": "com_int_total", "type": "grupo", "sign": "cost"},

    # ── Comisiones internas · Inmo Tradicional ──
    {"key": "com_intT_sellers", "label": "Sellers Inmo Tradicional", "parent": "com_int_trad", "type": "subcuenta", "sign": "cost"},
    {"key": "com_intT_buyers", "label": "Buyers Inmo Tradicional", "parent": "com_int_trad", "type": "subcuenta", "sign": "cost"},
    {"key": "com_int_trad", "label": "Comisiones Internas Inmo Tradicional", "parent": "com_int_total", "type": "grupo", "sign": "cost"},

    # ── Comisiones internas · Total ──
    {"key": "com_int_sellers", "label": "Sellers (Total)", "parent": "com_int_total", "type": "subcuenta", "sign": "cost"},
    {"key": "com_int_buyers", "label": "Buyers (Total)", "parent": "com_int_total", "type": "subcuenta", "sign": "cost"},
    {"key": "com_int_total", "label": "Comisión Equipos Internos", "parent": None, "type": "rubro", "sign": "cost"},

    # ── Costos totales y margen ──
    {"key": "direct_costs", "label": "(-) Costos Directos", "parent": None, "type": "rubro", "sign": "cost"},
    {"key": "cm_inmo100", "label": "Margen de Contribución Inmo 100", "parent": None, "type": "total", "sign": "net"},
    {"key": "cm_trad", "label": "Margen de Contribución Inmo Tradicional", "parent": None, "type": "total", "sign": "net"},
    {"key": "contribution_margin", "label": "(=) Margen de Contribución", "parent": None, "type": "total", "sign": "net"},
]


# ─────────────────────────────────────────────────────────────────────────────
# cálculo por vista
# ─────────────────────────────────────────────────────────────────────────────

def _line_values(df: pd.DataFrame, vista: str) -> dict[str, pd.Series]:
    """Devuelve dict {key → serie por-fila} de todas las líneas P&L Inmo.

    `vista` ∈ {'acc', 'sintetico'}.
    """
    is_sint = vista == "sintetico"

    def pick(ue_col: str, acc_col: str) -> pd.Series:
        """Sintético: coalesce(_ue, _accounting). ACC: solo _accounting."""
        if is_sint and ue_col in df.columns:
            return _coalesce_ue_acc(df, ue_col, acc_col)
        return _num(df[acc_col])

    is100 = _mask_inmo100(df).astype(float)
    isT = _mask_trad(df).astype(float)

    lines: dict[str, pd.Series] = {}

    # ── properties (conteos por producto) ──
    lines["properties_inmo100"] = is100
    lines["properties_trad"] = isT
    lines["properties_total"] = is100 + isT

    # ── GMV ──
    c_precio = _num(df["c_precio"])
    c_precio_sin = _num(df["c_precio_sin_inmo100"])
    lines["gmv_inmo100"] = c_precio * is100
    lines["gmv_trad"] = c_precio * isT
    lines["gmv_total"] = lines["gmv_inmo100"] + lines["gmv_trad"]
    lines["avg_ticket"] = c_precio  # se promedia en aggregate() dividiendo por properties_total
    # Fee HC100 = c_precio - c_precio_sin_inmo100. Solo aplica material a Inmo 100.
    lines["fee_hc100"] = (c_precio - c_precio_sin)

    # ── Gross Profit (comisión cobrada al cliente) ──
    gp = _num(df["comision_cobrada_accounting"])
    lines["gp_inmo100"] = gp * is100
    lines["gp_trad"] = gp * isT
    lines["gross_profit"] = lines["gp_inmo100"] + lines["gp_trad"]
    lines["avg_commission"] = gp  # se promedia en aggregate() dividiendo por properties_total
    # pct_fee_charged y pct_fee_paid se derivan post-agregación como
    # gross_profit / gmv_total y brokers / gmv_total respectivamente.
    # Placeholder aquí; el valor real se calcula en _post_avg.
    lines["pct_fee_charged"] = pd.Series(0.0, index=df.index)

    # ── Brokers externos ──
    # NOTA: el PDF dashboard interno usa `_infra` (budget), no `_accounting` (real).
    # Validado 2026-07-29 contra jun-2026: _infra=612.39k coincide 100% con PDF.
    brokers = -_num(df["comision_pagada_brokers_infra"])
    lines["brokers_inmo100"] = brokers * is100
    lines["brokers_trad"] = brokers * isT
    lines["brokers"] = lines["brokers_inmo100"] + lines["brokers_trad"]
    # Placeholder — el % real se deriva en _post_avg como |brokers| / gmv_total.
    lines["pct_fee_paid"] = pd.Series(0.0, index=df.index)

    # ── Gastos transaccionales (4 buckets) ──
    lines["tx_avaluo"] = -pick("avaluo_ue", "avaluo_accounting")
    lines["tx_notariales"] = -pick("gastos_notariales_ue", "gastos_notariales_accounting")
    lines["tx_apertura"] = -pick("apertura_expediente_ue", "apertura_expediente_accounting")
    lines["tx_inscripcion"] = -pick("inscripcion_credito_ue", "inscripcion_credito_accounting")
    lines["gastos_transaccionales"] = (
        lines["tx_avaluo"] + lines["tx_notariales"]
        + lines["tx_apertura"] + lines["tx_inscripcion"]
    )

    # ── Comisiones internas ──
    com_sellers = -_num(df["comision_sellers"])
    com_buyers = -_num(df["comision_buyers"])
    lines["com_int100_sellers"] = com_sellers * is100
    lines["com_int100_buyers"] = com_buyers * is100
    lines["com_int_inmo100"] = lines["com_int100_sellers"] + lines["com_int100_buyers"]
    lines["com_intT_sellers"] = com_sellers * isT
    lines["com_intT_buyers"] = com_buyers * isT
    lines["com_int_trad"] = lines["com_intT_sellers"] + lines["com_intT_buyers"]
    lines["com_int_sellers"] = com_sellers
    lines["com_int_buyers"] = com_buyers
    lines["com_int_total"] = com_sellers + com_buyers

    # ── Costos directos ──
    lines["direct_costs"] = (
        lines["brokers"] + lines["gastos_transaccionales"] + lines["com_int_total"]
    )
    # Margen contribución por producto. Cada NID lleva sus propios gastos
    # transaccionales (Kamila 2026-07-30: aunque el flujo típico de gastos
    # transaccionales es del producto Inmo 100, algunos Inmo Tradicional también
    # los tienen y se deben restar donde correspondan por NID).
    tx_gasto = lines["gastos_transaccionales"]
    lines["cm_inmo100"] = (
        lines["gp_inmo100"] + lines["brokers_inmo100"] + lines["com_int_inmo100"]
        + tx_gasto * is100  # gastos transaccionales SOLO de NIDs Inmo 100
    )
    lines["cm_trad"] = (
        lines["gp_trad"] + lines["brokers_trad"] + lines["com_int_trad"]
        + tx_gasto * isT  # gastos transaccionales SOLO de NIDs Tradicional
    )
    lines["contribution_margin"] = lines["gross_profit"] + lines["direct_costs"]

    return lines


def line_values_per_nid(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """DataFrame por-NID con [nid, region, mes, <key1>, <key2>, ...]."""
    lines = _line_values(df_prepared, vista)
    wide = pd.DataFrame(lines)
    wide.insert(0, "mes", df_prepared["mes"].values)
    wide.insert(0, "region", df_prepared["region_norm"].values)
    wide.insert(0, "nid", df_prepared["nid"].values)
    return wide


# Columnas cuyo agregado es promedio ponderado por properties_total (NIDs).
AVG_COLUMNS = {
    "avg_ticket": "properties_total",
    "avg_commission": "properties_total",
}

# Columnas derivadas: se calculan post-agregación como ratio de otras dos.
# Formato: {col_target: (col_numerador, col_denominador, abs_numerator)}
DERIVED_COLUMNS = {
    "pct_fee_charged": ("gross_profit", "gmv_total", False),
    "pct_fee_paid": ("brokers", "gmv_total", True),  # brokers es negativo → tomar abs
}


def _post_avg(grouped: pd.DataFrame) -> pd.DataFrame:
    """Convierte SUM(col)/SUM(count) para columnas de promedio, y calcula
    los ratios derivados como pct_fee_charged / pct_fee_paid."""
    for col, count_col in AVG_COLUMNS.items():
        if col in grouped.columns and count_col in grouped.columns:
            denom = grouped[count_col].where(grouped[count_col] > 0, other=pd.NA)
            grouped[col] = (grouped[col] / denom).fillna(0.0)
    for col, (num_col, den_col, use_abs) in DERIVED_COLUMNS.items():
        if col in grouped.columns and num_col in grouped.columns and den_col in grouped.columns:
            num = grouped[num_col].abs() if use_abs else grouped[num_col]
            denom = grouped[den_col].where(grouped[den_col] > 0, other=pd.NA)
            grouped[col] = (num / denom).fillna(0.0)
    return grouped


def aggregate(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Devuelve DataFrame long: [region, mes, key, valor]."""
    lines = _line_values(df_prepared, vista)
    wide = pd.DataFrame(lines)
    wide["region"] = df_prepared["region_norm"].values
    wide["mes"] = df_prepared["mes"].values
    grouped = wide.groupby(["region", "mes"], as_index=False).sum(numeric_only=True)
    grouped = _post_avg(grouped)
    long = grouped.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")
    return long


def aggregate_all_regions(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Igual a aggregate pero también añade fila 'Total' (todas las regiones)."""
    by_region = aggregate(df_prepared, vista)
    lines = _line_values(df_prepared, vista)
    wide = pd.DataFrame(lines)
    wide["mes"] = df_prepared["mes"].values
    total = wide.groupby("mes", as_index=False).sum(numeric_only=True)
    total = _post_avg(total)
    total["region"] = "Total"
    total_long = total.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")
    return pd.concat([by_region, total_long], ignore_index=True)
