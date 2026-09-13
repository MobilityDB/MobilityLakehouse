-- Query 8, nearest approach: the closest distance reached between any two vessels in the belt
-- during the window (candidate pairs gated at two kilometres).
SELECT round(min(nearestApproachDistance(t1, t2))::numeric) AS min_m FROM cand;
