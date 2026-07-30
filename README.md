# mx-inmo-pnl-dash

Dashboard estático de **P&L Inmobiliaria por ciudad · México** (Habi).

- **Fuente**: `clients-domain-data-master.finance_wh_bi.finance_apartment_tracker_inmobiliaria_mx`
- **Cohorte**: `c_fecha_factura_netsuite` (facturación NetSuite Inmo)
- **Producto**: Inmo 100 (`linea_negocio_hc100='Si'`) vs Inmo Tradicional (`='No'`)
- **Currency**: MXN 000's
- **Alcance**: hasta Margen de Contribución (por producto + total)
- **Dos vistas**: ACC (`_accounting`) · Sintético (`_ue` con fallback `_accounting` fila por fila)

## Comandos

```bash
make install     # una vez
make raw         # trae raw de BQ → data/raw_apartment_inmo_mx.parquet
make refresh     # raw + agrega P&L → site/data/kpi_pnl.json
make serve       # http://localhost:8004/site/
```

## Prerequisitos

```bash
gcloud auth application-default login
```

## Deploy

GitHub Pages sobre `main`. Workflow en `.github/workflows/pages.yml` publica solo `site/`.

## Estructura del P&L Inmo

```
Properties Inmo 100 / Tradicional / Total  (count)
GMV Inmo 100 + Fee HC100 = GMV Inmobiliaria (base de units)
Gross Profit por producto + Avg. Ticket + Avg. Commission + % fee charged
(-) Comisión Brokers (por producto) + % fee paid
(-) Gastos Transaccionales (Avalúos + Notariales + Apertura + Inscripción)
(-) Comisiones Internas (Sellers/Buyers por producto)
(=) Costos Directos
(=) Margen de Contribución por producto = suma de partes = Total
```

## Rescate de regiones sin etiqueta

El tracker tiene 46% de NIDs con `region=NULL`. Se rescatan por
`ciudad` (diccionario en `_pnl.py`) + fallback a `area_metropolitana`.
Resultado: 100% de NIDs con región asignada (paridad con MM MX).

## Notas técnicas

- Brokers usa `comision_pagada_brokers_infra` (budget), NO `_accounting` (real).
  Validado 2026-07-29 contra dashboard interno Corporate_Finance_Master.
- `% fee charged` = `Gross Profit / GMV Total` (fórmula matemática limpia).
- `% fee paid` = `|Brokers| / GMV Total`.
- `Fee HC100` = `c_precio - c_precio_sin_inmo100` (aunque la columna se llame
  "inmo100" es el equivalente conceptual del fee HC100 del MM).
