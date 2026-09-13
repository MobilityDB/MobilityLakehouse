/*****************************************************************************
 *
 * SparkQueries.java
 *
 * Blocks of Spark SQL over a table of trips, with the answer of each.
 *
 * The trips are the Parquet files named on the command line, read into the view `trips`, and the
 * MEOS functions are MobilitySpark's generated surface, registered on the session. Standard input
 * carries the blocks: a line `@@QUERY <label>` starts one, and its statements follow, each ending
 * with a semicolon at the end of a line. The first row of the last statement is the block's
 * answer, printed as `@@ANSWER <label>` and a tab and its fields joined by `|`, a null field as
 * `NULL`, the way DuckDB prints an answer; a block that fails prints `@@ERROR <label>` and a tab
 * and the message instead. The session runs in UTC.
 *
 *   java -cp <build>:<MobilitySpark classes and classpath> SparkQueries <parquet files> < blocks
 *
 *****************************************************************************/
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.StringJoiner;

import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.Row;
import org.apache.spark.sql.SparkSession;
import org.mobilitydb.spark.generated.GeneratedSpatioTemporalUDFs;

public final class SparkQueries {

    private SparkQueries() {}

    public static void main(String[] args) throws IOException {
        SparkSession spark = SparkSession.builder().appName("lakehouse-queries")
                .master(System.getProperty("spark.master", "local[*]"))
                .config("spark.ui.enabled", "false")
                .config("spark.sql.session.timeZone", "UTC")
                // A row carries a whole trajectory, so a small columnar batch keeps each buffer
                // of the Parquet reader far below the heap
                .config("spark.sql.parquet.columnarReaderBatchSize", "256")
                .getOrCreate();
        spark.sparkContext().setLogLevel("WARN");
        GeneratedSpatioTemporalUDFs.registerAll(spark);
        spark.read().parquet(args).createOrReplaceTempView("trips");

        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String label = null;
        StringBuilder block = new StringBuilder();
        String line;
        while ((line = in.readLine()) != null) {
            if (line.startsWith("@@QUERY ")) {
                if (label != null) {
                    run(spark, label, block.toString());
                }
                label = line.substring("@@QUERY ".length()).trim();
                block.setLength(0);
            } else {
                block.append(line).append('\n');
            }
        }
        if (label != null) {
            run(spark, label, block.toString());
        }
        spark.stop();
    }

    /** Run one block's statements in order and print the first row of the last one */
    private static void run(SparkSession spark, String label, String text) {
        try {
            Dataset<Row> last = null;
            for (String statement : text.split(";\\s*\\n")) {
                if (!statement.trim().isEmpty()) {
                    last = spark.sql(statement);
                }
            }
            String answer = "";
            if (last != null) {
                List<Row> rows = last.limit(1).collectAsList();
                if (!rows.isEmpty()) {
                    Row row = rows.get(0);
                    StringJoiner fields = new StringJoiner("|");
                    for (int i = 0; i < row.length(); i++) {
                        fields.add(row.isNullAt(i) ? "NULL" : String.valueOf(row.get(i)));
                    }
                    answer = fields.toString();
                }
            }
            System.out.println("@@ANSWER " + label + "\t" + answer);
        } catch (Exception e) {
            System.out.println("@@ERROR " + label + "\t" + String.valueOf(e.getMessage()).replace('\n', ' '));
        }
        System.out.flush();
    }
}
