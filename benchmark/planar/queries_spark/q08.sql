-- Query 8, nearest approach: the closest distance reached between any two vessels in the belt
-- during the window (candidate pairs gated at two kilometres).
SELECT CAST(min(nearestApproachDistance(t1, t2)) AS DECIMAL(20, 0)) AS min_m FROM cand;
