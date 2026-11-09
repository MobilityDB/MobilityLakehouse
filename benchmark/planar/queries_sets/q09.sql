-- Query 9, the set: the vessel pairs that come within 300 m of each other in the belt during the
-- window.
SELECT DISTINCT m1, m2 FROM Cand WHERE eDwithin(t1, t2, 300) ORDER BY 1, 2;
