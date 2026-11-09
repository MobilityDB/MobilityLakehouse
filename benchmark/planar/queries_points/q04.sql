-- Query 4 over the point table, fleet summary: the total length of the legs in the belt during
-- the window, and how many distinct vessels and ship types reported a position there.
SELECT round(sum(sqrt((xb - xa) ^ 2 + (yb - ya) ^ 2)) / 1000.0, 1) AS total_km,
  (SELECT COUNT(DISTINCT MMSI) FROM mp) AS n_vessels,
  (SELECT COUNT(DISTINCT ship_type) FROM mp) AS n_types
FROM legs;
