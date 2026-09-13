-- Query 4, fleet summary: the total distance travelled in the belt during the window, and how
-- many distinct vessels and ship types were involved.
SELECT round((sum(length(g)) / 1000.0)::numeric, 1) AS total_km,
  count(DISTINCT mmsi) AS n_vessels, count(DISTINCT ship_type) AS n_types
FROM clipped;
