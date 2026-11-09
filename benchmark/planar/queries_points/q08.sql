-- Query 8 over the point table, nearest approach: the closest distance reached between any two
-- vessels in the belt during the window (candidate pairs gated at two kilometres).
SELECT round(MIN(dist)) AS min_m FROM Cand;
