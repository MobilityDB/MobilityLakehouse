-- Query 8, nearest approach: the closest distance reached between any two vessels in the belt
-- during the window (candidate pairs gated at two kilometres).
SELECT round(MIN(nearestApproachDistance(t1, t2))) AS min_m FROM Cand;
