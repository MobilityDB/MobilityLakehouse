-- Load the WDPA protected areas, as the AIS benchmark's own loader does (SRID 25832), and keep
-- their lon/lat form beside it, since H3 is defined on geographic coordinates and the figures are
-- drawn in EPSG:25832.
--
-- Takes :natural_areas, the gzipped WDPA export.
\set gzcmd 'gzip -dc ' :'natural_areas'

DROP TABLE IF EXISTS NaturalAreas CASCADE;
CREATE TABLE NaturalAreas (
  Id integer PRIMARY KEY, SiteId integer, SitePid varchar(52), SiteType varchar(80),
  NameEng varchar(80), Name varchar(80), Desig varchar(80), DesigEng varchar(80),
  DesigType varchar(80), IucnCat varchar(80), IntCrit varchar(80), Realm varchar(20),
  RepMArea numeric, GisMArea numeric, RepArea numeric, GisArea numeric,
  NoTake varchar(80), NoTkArea numeric, Status varchar(80), StatusYr integer,
  GovType varchar(80), GovSubType varchar(80), OwnType varchar(80), OwnSubType varchar(80),
  MangAuth varchar(169), MangPlan varchar(169), Verif varchar(80), Metadataid integer,
  PrntISO3 varchar(80), ISO3 varchar(80), SuppInfo varchar(80), ConsObj varchar(80),
  InlndWtrs varchar(80), OecmAsmt varchar(80), Geom geometry(MultiPolygon,25832)
);
COPY NaturalAreas FROM PROGRAM :'gzcmd' WITH (FORMAT csv, HEADER true, DELIMITER ',');

ALTER TABLE NaturalAreas ADD COLUMN GeomLL geometry(MultiPolygon,4326);
UPDATE NaturalAreas SET GeomLL = ST_Transform(Geom, 4326);
CREATE INDEX ON NaturalAreas USING gist (GeomLL);
SELECT count(*) FROM NaturalAreas;
