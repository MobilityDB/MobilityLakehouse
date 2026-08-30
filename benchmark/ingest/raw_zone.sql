-- raw_zone.sql — one day of Danish Maritime Authority AIS CSV into one Parquet file.
--
-- The raw zone is a faithful columnar copy: every source column survives, the types are
-- the ones the values already have, and nothing is filtered, deduplicated or projected.
-- Cleaning and segmentation read this zone; they never read the CSV again, so a change
-- to a cleaning threshold costs a re-read of Parquet rather than a re-download.
--
-- Column names are the canonical AIS names the MobilityDB AIS loaders use, so a table
-- from this zone and a table from those loaders answer the same column names. The DMA
-- header spells them differently ("# Timestamp", "Type of mobile", ...) and positionally;
-- `columns` renames by position and `header = true` drops the source header row.
--
-- Parameter, set by the caller:
--   csv_path   the decompressed CSV for one day
--
-- This file defines the view and therefore owns the column contract; the caller issues the
-- `COPY ais_raw TO ...` because DuckDB's COPY takes a literal destination and will not read
-- one from a variable.
--
-- ETA stays VARCHAR. It is a reported field, frequently blank or malformed, and coercing
-- it here would be interpretation the raw zone does not do; a consumer that wants a
-- timestamp casts it and decides what a failed cast means.

CREATE OR REPLACE VIEW ais_raw AS
  SELECT *
  FROM read_csv(
    getvariable('csv_path'),
    header = true,
    timestampformat = '%d/%m/%Y %H:%M:%S',
    -- Vessel names carry quotes, and the feed escapes them the RFC 4180 way by doubling:
    -- `"""SS"" MARTHA"` is the name `"SS" MARTHA`. The sniffer infers escape = (empty) from
    -- the first rows, where no such name appears, and then rejects the first one that does
    -- and then rejects the first one that does, with "Value with unterminated quote found".
    -- Stating the escape reads those names correctly. NOT `ignore_errors`: that drops the
    -- row instead, and a zone whose contract is a faithful copy may not silently lose a
    -- vessel to its own name.
    escape = '"',
    columns = {
      'T':                          'TIMESTAMP',
      'TypeOfMobile':               'VARCHAR',
      'MMSI':                       'BIGINT',
      'Latitude':                   'DOUBLE',
      'Longitude':                  'DOUBLE',
      'NavigationalStatus':         'VARCHAR',
      'ROT':                        'DOUBLE',
      'SOG':                        'DOUBLE',
      'COG':                        'DOUBLE',
      'Heading':                    'INTEGER',
      'IMO':                        'VARCHAR',
      'CallSign':                   'VARCHAR',
      'Name':                       'VARCHAR',
      'ShipType':                   'VARCHAR',
      'CargoType':                  'VARCHAR',
      'Width':                      'DOUBLE',
      'Length':                     'DOUBLE',
      'TypeOfPositionFixingDevice': 'VARCHAR',
      'Draught':                    'DOUBLE',
      'Destination':                'VARCHAR',
      'ETA':                        'VARCHAR',
      'DataSourceType':             'VARCHAR',
      'SizeA':                      'DOUBLE',
      'SizeB':                      'DOUBLE',
      'SizeC':                      'DOUBLE',
      'SizeD':                      'DOUBLE'
    }
  );
