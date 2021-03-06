package config

import (
	"time"
)

// QuerySince holds the start time we have data since.
// Don't consider data before this time. May be due to schema changes, etc.
// Note that the limit is exclusive, this date does not contain good data.
type QuerySince time.Time

// Date returns QuerySince in the YearMonDay format.
func (b QuerySince) Date() string {
	return time.Time(b).Format("20060102")
}

// Unix returns the unix timestamp.
func (b QuerySince) Unix() int64 {
	return time.Time(b).Unix()
}

func NewQuerySince(t time.Time) QuerySince {
	return QuerySince(t)
}

const (
	// TILE_SCALE The number of points to subsample when moving one level of scaling. I.e.
	// a tile at scale 1 will contain every 4th point of the tiles at scale 0.
	TILE_SCALE = 4

	// The number of samples per trace in a tile, i.e. the number of git hashes that have data
	// in a single tile.
	TILE_SIZE = 128

	// JSON doesn't support NaN or +/- Inf, so we need a valid float
	// to signal missing data that also has a compact JSON representation.
	MISSING_DATA_SENTINEL = 1e100

	// Limit the number of commits we hold in memory and do bulk analysis on.
	MAX_COMMITS_IN_MEMORY = 32

	// Limit the number of times the ingester tries to get a file before giving up.
	MAX_URI_GET_TRIES = 4

	// MAX_SAMPLE_TRACES_PER_CLUSTER  is the maximum number of traces stored in a
	// ClusterSummary.
	MAX_SAMPLE_TRACES_PER_CLUSTER = 1

	RECLUSTER_DURATION = 15 * time.Minute

	// CLUSTER_COMMITS is the number of commits to use when clustering.
	MAX_CLUSTER_COMMITS = TILE_SIZE

	// MIN_CLUSTER_STEP_COMMITS is minimum number of commits that we need on either leg
	// of a step function.
	MIN_CLUSTER_STEP_COMMITS = 5

	// MIN_STDDEV is the smallest standard deviation we will normalize, smaller
	// than this and we presume it's a standard deviation of zero.
	MIN_STDDEV = 0.001
)

const (
	// Different datasets that are stored in tiles.
	DATASET_GOLD = "gold"
	DATASET_NANO = "nano"

	// Constructor names that are used to instantiate an ingester.
	// Note that, e.g. 'android-gold' has a different ingester, but writes
	// to the gold dataset.
	CONSTRUCTOR_NANO         = DATASET_NANO
	CONSTRUCTOR_GOLD         = DATASET_GOLD
	CONSTRUCTOR_NANO_TRYBOT  = "nano-trybot"
	CONSTRUCTOR_ANDROID_GOLD = "android-gold"
)

var (
	VALID_DATASETS = []string{
		DATASET_NANO,
		DATASET_GOLD,
	}
)

var (
	// TODO(jcgregorio) Make into a flag.
	BEGINNING_OF_TIME = QuerySince(time.Date(2014, time.June, 18, 0, 0, 0, 0, time.UTC))
)
