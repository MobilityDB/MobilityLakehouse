-- Query 9 over the point table, collision: how many vessel pairs came within 300 m of each other
-- in the belt during the window (gate 300 m)?
SELECT COUNT(DISTINCT (m1, m2)) AS n_pairs_300m FROM Cand WHERE dist <= 300;
