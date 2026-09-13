-- Query 4, fleet summary: the total distance travelled in the belt during the window, and how
-- many distinct vessels and ship types were involved.
SELECT CAST(sum(length(g)) / 1000.0 AS DECIMAL(20, 1)) AS total_km,
  count(DISTINCT mmsi) AS n_vessels, count(DISTINCT ship_type) AS n_types
FROM clipped;
