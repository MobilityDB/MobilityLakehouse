-- Query 9, collision: how many vessel pairs came within 300 m of each other in the belt during
-- the window (gate 300 m)?
SELECT count(DISTINCT m1, m2) AS n_pairs_300m FROM cand
WHERE nearestApproachDistance(t1, t2) < 300;
