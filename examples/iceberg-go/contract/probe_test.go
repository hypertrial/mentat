// Licensed under the Apache License, Version 2.0. See NOTICE.
package table_test

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/apache/arrow-go/v18/arrow"
	"github.com/apache/arrow-go/v18/arrow/array"
	"github.com/apache/arrow-go/v18/arrow/memory"
	"github.com/apache/arrow-go/v18/parquet"
	"github.com/apache/arrow-go/v18/parquet/pqarrow"
	"github.com/apache/iceberg-go"
	iceio "github.com/apache/iceberg-go/io"
	"github.com/apache/iceberg-go/table"
	"github.com/apache/iceberg-go/table/compaction"
	"github.com/stretchr/testify/require"
)

type mentatCatalog struct {
	metadata table.Metadata
	commits  []table.Metadata
}

func (c *mentatCatalog) LoadTable(context.Context, table.Identifier) (*table.Table, error) {
	return nil, nil
}
func (c *mentatCatalog) CommitTable(_ context.Context, _ table.Identifier, _ []table.Requirement, updates []table.Update) (table.Metadata, string, error) {
	meta, err := table.UpdateTableMetadata(c.metadata, updates, "")
	if err != nil {
		return nil, "", err
	}
	c.metadata = meta
	c.commits = append(c.commits, meta)
	return meta, "", nil
}

type mentatRow struct {
	ID   int64  `json:"id"`
	Data string `json:"data"`
}
type mentatObservation struct {
	Initial        []mentatRow   `json:"initial_rows"`
	Snapshots      [][]mentatRow `json:"committed_rows"`
	Final          []mentatRow   `json:"final_rows"`
	OperationError string        `json:"operation_error"`
}

func mentatScan(t *testing.T, tbl *table.Table, snapshot int64) []mentatRow {
	t.Helper()
	_, records, err := tbl.Scan(table.WithSnapshotID(snapshot)).ToArrowRecords(t.Context())
	require.NoError(t, err)
	rows := []mentatRow{}
	for rec, err := range records {
		require.NoError(t, err)
		ids := rec.Column(rec.Schema().FieldIndices("id")[0]).(*array.Int64)
		data := rec.Column(rec.Schema().FieldIndices("data")[0]).(*array.String)
		for i := range ids.Len() {
			require.False(t, ids.IsNull(i))
			require.False(t, data.IsNull(i))
			rows = append(rows, mentatRow{ids.Value(i), data.Value(i)})
		}
		rec.Release()
	}
	return rows
}
func TestMentatCompactionRows(t *testing.T) {
	observations := map[string]mentatObservation{}
	for _, split := range []bool{false, true} {
		for _, partial := range []bool{false, true} {
			for _, filtered := range []bool{false, true} {
				name := fmt.Sprintf("split=%t/partial=%t/filtered=%t", split, partial, filtered)
				t.Run(name, func(t *testing.T) {
					location := filepath.ToSlash(t.TempDir())
					schema := iceberg.NewSchema(0, iceberg.NestedField{ID: 1, Name: "id", Type: iceberg.PrimitiveTypes.Int64, Required: true}, iceberg.NestedField{ID: 2, Name: "data", Type: iceberg.PrimitiveTypes.String})
					meta, err := table.NewMetadata(schema, iceberg.UnpartitionedSpec, table.UnsortedSortOrder, location, iceberg.Properties{table.PropertyFormatVersion: "2"})
					require.NoError(t, err)
					cat := &mentatCatalog{metadata: meta}
					tbl := table.New(table.Identifier{"db", "mentat"}, meta, location+"/metadata/v1.metadata.json", func(context.Context) (iceio.IO, error) { return iceio.LocalFS{}, nil }, cat)
					sc, err := table.SchemaToArrowSchema(schema, nil, false, false)
					require.NoError(t, err)
					var input []string
					for i := 1; i <= 12; i++ {
						input = append(input, fmt.Sprintf(`{"id":%d,"data":"a"}`, i))
					}
					rec, _, err := array.RecordFromJSON(memory.DefaultAllocator, sc, strings.NewReader("["+strings.Join(input, ",")+"]"))
					require.NoError(t, err)
					path := location + "/data/split.parquet"
					fw, err := (iceio.LocalFS{}).Create(path)
					require.NoError(t, err)
					arrowTable := array.NewTableFromRecords(sc, []arrow.RecordBatch{rec})
					require.NoError(t, pqarrow.WriteTable(arrowTable, fw, 1, parquet.NewWriterProperties(parquet.WithStats(true)), pqarrow.DefaultWriterProps()))
					arrowTable.Release()
					rec.Release()
					size := "999999999"
					if split {
						size = "1"
					}
					tx := tbl.NewTransaction()
					require.NoError(t, tx.SetProperties(iceberg.Properties{table.ReadSplitTargetSizeKey: size}))
					require.NoError(t, tx.AddFiles(t.Context(), []string{path}, nil, false))
					tbl, err = tx.Commit(t.Context())
					require.NoError(t, err)
					baseline := len(cat.commits)
					initial := mentatScan(t, tbl, tbl.CurrentSnapshot().SnapshotID)
					scan := tbl.Scan()
					filter := iceberg.GreaterThan(iceberg.Reference("id"), int64(6))
					if filtered {
						scan = tbl.Scan(table.WithRowFilter(filter))
					}
					tasks, err := scan.PlanFiles(t.Context())
					require.NoError(t, err)
					if split {
						require.Len(t, tasks, 12)
					} else {
						require.Len(t, tasks, 1)
					}
					if filtered {
						for i := range tasks {
							tasks[i].Residual = filter
						}
					}
					fileSize := tasks[0].File.FileSizeBytes()
					cfg := compaction.DefaultConfig()
					cfg.TargetFileSizeBytes = fileSize + 1
					cfg.MinFileSizeBytes = fileSize + 1
					cfg.MaxFileSizeBytes = fileSize * 2
					cfg.MinInputFiles = 1
					var groups []compaction.Group
					if filtered {
						plan, e := cfg.PlanCompaction(tasks)
						require.NoError(t, e)
						groups = plan.Groups
					} else {
						plan, e := compaction.Analyze(t.Context(), tbl, cfg)
						require.NoError(t, e)
						groups = plan.Groups
					}
					taskGroups := make([]table.CompactionTaskGroup, len(groups))
					for i, g := range groups {
						taskGroups[i] = table.CompactionTaskGroup{PartitionKey: g.PartitionKey, Tasks: g.Tasks, TotalSizeBytes: g.TotalSizeBytes}
					}
					_, opErr := tbl.RewriteDataFiles(t.Context(), taskGroups, table.RewriteDataFilesOptions{PartialProgress: partial, MaxCommits: 12})
					obs := mentatObservation{Initial: initial, Snapshots: [][]mentatRow{}}
					if opErr != nil {
						obs.OperationError = opErr.Error()
					}
					for _, m := range cat.commits[baseline:] {
						if s := m.CurrentSnapshot(); s != nil {
							obs.Snapshots = append(obs.Snapshots, mentatScan(t, table.New(tbl.Identifier(), m, tbl.MetadataLocation(), func(context.Context) (iceio.IO, error) { return iceio.LocalFS{}, nil }, cat), s.SnapshotID))
						}
					}
					current := table.New(tbl.Identifier(), cat.metadata, tbl.MetadataLocation(), func(context.Context) (iceio.IO, error) { return iceio.LocalFS{}, nil }, cat)
					require.NotNil(t, current.CurrentSnapshot())
					obs.Final = mentatScan(t, current, current.CurrentSnapshot().SnapshotID)
					observations[name] = obs
				})
			}
		}
	}
	data, err := json.Marshal(map[string]any{"schema_version": 1, "go_version": runtime.Version(), "cases": observations})
	require.NoError(t, err)
	fmt.Printf("MENTAT_OBSERVATIONS=%s\n", data)
}
