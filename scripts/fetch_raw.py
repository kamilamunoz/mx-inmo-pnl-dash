"""Trae el raw de finance_apartment_tracker_inmobiliaria_mx y lo guarda como parquet.

Convenciones Inmo MX (distintas al MM):
- Fecha canónica facturación: `c_fecha_factura_netsuite` (DATE) — no `fecha_facturacion_venta` ni `c_fecha_factura`.
- Producto Inmo 100 vs Inmo Tradicional se determina por `linea_negocio_hc100` ('Si' / 'No').
  El campo `tipo_de_inmobiliaria` NO clasifica el producto — es ruido (misma etiqueta
  puede contener HC100=Si y No). Validado 2026-07-29 contra dashboard interno.
- Fee HC100 = `c_precio - c_precio_sin_inmo100` (analogo al MM, con nombre distinto).

Uso:
    make raw
"""

from __future__ import annotations

import logging
from pathlib import Path

from scripts._bq import BILLING_PROJECT, TABLE_APT_INMO_MX, run_query

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "data" / "raw_apartment_inmo_mx.parquet"

QUERY = f"""
SELECT
    -- id + territorio
    nid,
    region,
    ciudad,
    area_metropolitana,

    -- fechas
    c_fecha_factura_netsuite,
    c_fecha_promesa,
    c_fecha_escritura,
    fecha_captacion,
    v_fecha_publicacion,

    -- producto (canónico)
    linea_negocio_hc100,
    tipo_de_inmobiliaria,

    -- precio
    c_precio,
    c_precio_sin_inmo100,
    v_precio,

    -- gross profit / comisión cobrada al cliente
    comision_cobrada_accounting,
    comision_cobrada_mg,
    porcentaje_recibido,

    -- brokers externos
    comision_pagada_brokers_accounting,
    comision_pagada_brokers_infra,
    porcentaje_pagada_brokers_accounting,
    porcentaje_pagado_brokers_infra,

    -- trámites transaccionales — 4 buckets del PDF
    avaluo_accounting,
    avaluo_model,
    avaluo_ue,
    gastos_notariales_accounting,
    gastos_notariales_model,
    gastos_notariales_ue,
    apertura_expediente_accounting,
    apertura_expediente_model,
    apertura_expediente_ue,
    inscripcion_credito_accounting,
    inscripcion_credito_model,
    inscripcion_credito_ue,
    tramiti_accounting,

    -- comisiones internas
    comision_sellers,
    comision_buyers,
    porcentaje_comision_sellers,
    porcentaje_comision_buyers,

    -- metadata operativo
    v_comercial,
    v_coordinador
FROM `{TABLE_APT_INMO_MX}`
WHERE c_fecha_factura_netsuite IS NOT NULL
  AND linea_negocio_hc100 IN ('Si', 'No')
"""


def main() -> None:
    log.info("Trayendo raw de %s (billing=%s) ...", TABLE_APT_INMO_MX, BILLING_PROJECT)
    df = run_query(QUERY, label="apartment_inmo_mx_raw")
    log.info("Total filas: %d", len(df))
    log.info(
        "Rango c_fecha_factura_netsuite: %s → %s",
        df["c_fecha_factura_netsuite"].min(), df["c_fecha_factura_netsuite"].max(),
    )
    log.info(
        "Producto (linea_negocio_hc100): %d Inmo 100 · %d Inmo Tradicional",
        (df["linea_negocio_hc100"] == "Si").sum(),
        (df["linea_negocio_hc100"] == "No").sum(),
    )
    log.info("Regiones únicas: %d · con NULL: %d", df["region"].nunique(dropna=True), df["region"].isna().sum())
    log.info("Ciudades únicas: %d", df["ciudad"].nunique(dropna=True))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PATH, index=False)
    log.info("Escrito → %s (%.1f MB)", OUT_PATH, OUT_PATH.stat().st_size / 1024**2)


if __name__ == "__main__":
    main()
