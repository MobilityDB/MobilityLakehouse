-- Query 10, encounter zone: how many vessel pairs came within 500 m of each other in the belt
-- during the window (gate 500 m)?
SELECT count(DISTINCT m1, m2) AS n_pairs_500m FROM cand
WHERE nearestApproachDistance(t1, t2) < 500;
