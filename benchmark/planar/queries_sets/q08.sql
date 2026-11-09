-- Query 8, the values: the closest approach of each vessel pair within the two kilometres of the
-- gate, over the pieces of the two vessels.
SELECT m1, m2, MIN(nearestApproachDistance(t1, t2)) AS d FROM Cand
GROUP BY m1, m2 HAVING MIN(nearestApproachDistance(t1, t2)) <= 2000 ORDER BY 1, 2;
