package parser

import (
	"fmt"
	"math"
	"net/url"
	"strconv"

	"go.skia.org/infra/perf/go/config"
	"go.skia.org/infra/perf/go/types"
	"go.skia.org/infra/perf/go/vec"
)

type FilterFunc struct{}

// filterFunc is a Func that returns a filtered set of Traces from the Tile in
// the Context.
//
// It expects a single argument that is a string in URL query format, ala
// os=Ubuntu12&config=8888.
func (FilterFunc) Eval(ctx *Context, node *Node) ([]*types.PerfTrace, error) {
	if len(node.Args) != 1 {
		return nil, fmt.Errorf("filter() takes a single argument.")
	}
	if node.Args[0].Typ != NodeString {
		return nil, fmt.Errorf("filter() takes a string argument.")
	}
	query, err := url.ParseQuery(node.Args[0].Val)
	if err != nil {
		return nil, fmt.Errorf("filter() arg not a valid URL query parameter: %s", err)
	}
	traces := []*types.PerfTrace{}
	for id, tr := range ctx.Tile.Traces {
		if types.Matches(tr.(*types.PerfTrace), query) {
			cp := tr.DeepCopy()
			cp.Params()["id"] = types.AsCalculatedID(id)
			traces = append(traces, cp.(*types.PerfTrace))
		}
	}
	return traces, nil
}

func (FilterFunc) Describe() string {
	return `filter() returns a filtered set of Traces that match the given query.

  It expects a single argument that is a string in URL query format, such as:

     os=Ubuntu12&config=8888.`
}

var filterFunc = FilterFunc{}

type NormFunc struct{}

// normFunc implements Func and normalizes the traces to a mean of 0 and a
// standard deviation of 1.0. If a second optional number is passed in to
// norm() then that is used as the minimum standard deviation that is
// normalized, otherwise it defaults to config.MIN_STDDEV.
func (NormFunc) Eval(ctx *Context, node *Node) ([]*types.PerfTrace, error) {
	if len(node.Args) > 2 || len(node.Args) == 0 {
		return nil, fmt.Errorf("norm() takes one or two arguments.")
	}
	if node.Args[0].Typ != NodeFunc {
		return nil, fmt.Errorf("norm() takes a function as its first argument.")
	}
	minStdDev := config.MIN_STDDEV
	if len(node.Args) == 2 {
		if node.Args[1].Typ != NodeNum {
			return nil, fmt.Errorf("norm() takes a number as its second argument.")
		}
		var err error
		minStdDev, err = strconv.ParseFloat(node.Args[1].Val, 64)
		if err != nil {
			return nil, fmt.Errorf("norm() stddev not a valid number %s : %s", node.Args[1].Val, err)
		}
	}
	traces, err := node.Args[0].Eval(ctx)
	if err != nil {
		return nil, fmt.Errorf("norm() failed evaluating argument: %s", err)
	}

	for _, tr := range traces {
		vec.Norm(tr.Values, minStdDev)
	}

	return traces, nil
}

func (NormFunc) Describe() string {
	return `norm() normalizes the traces to a mean of 0 and a standard deviation of 1.0.

  If a second optional number is passed in to
  norm() then that is used as the minimum standard deviation that is
  normalized, otherwise it defaults to 0.1.`
}

var normFunc = NormFunc{}

type FillFunc struct{}

// fillFunc implements Func and fills in all the missing datapoints with nearby
// points.
//
// Note that a Trace with all MISSING_DATA_SENTINEL values will be filled with
// 0's.
func (FillFunc) Eval(ctx *Context, node *Node) ([]*types.PerfTrace, error) {
	if len(node.Args) != 1 {
		return nil, fmt.Errorf("fill() takes a single argument.")
	}
	if node.Args[0].Typ != NodeFunc {
		return nil, fmt.Errorf("fill() takes a function argument.")
	}
	traces, err := node.Args[0].Eval(ctx)
	if err != nil {
		return nil, fmt.Errorf("fill() failed evaluating argument: %s", err)
	}

	for _, tr := range traces {
		vec.Fill(tr.Values)
	}
	return traces, nil
}

func (FillFunc) Describe() string {
	return `fill() fills in all the missing datapoints with nearby points.

  Data can be missing because buildbots may roll mulitiple commits into a single run.`
}

var fillFunc = FillFunc{}

type AveFunc struct{}

// aveFunc implements Func and averages the values of all argument
// traces into a single trace.
//
// MISSING_DATA_SENTINEL values are not included in the average.  Note that if
// all the values at an index are MISSING_DATA_SENTINEL then the average will
// be MISSING_DATA_SENTINEL.
func (AveFunc) Eval(ctx *Context, node *Node) ([]*types.PerfTrace, error) {
	if len(node.Args) != 1 {
		return nil, fmt.Errorf("ave() takes a single argument.")
	}
	if node.Args[0].Typ != NodeFunc {
		return nil, fmt.Errorf("ave() takes a function argument.")
	}
	traces, err := node.Args[0].Eval(ctx)
	if err != nil {
		return nil, fmt.Errorf("ave() argument failed to evaluate: %s", err)
	}

	if len(traces) == 0 {
		return traces, nil
	}

	ret := types.NewPerfTraceN(len(traces[0].Values))
	ret.Params()["id"] = types.AsFormulaID(ctx.formula)
	for i, _ := range ret.Values {
		sum := 0.0
		count := 0
		for _, tr := range traces {
			if v := tr.Values[i]; v != config.MISSING_DATA_SENTINEL {
				sum += v
				count += 1
			}
		}
		if count > 0 {
			ret.Values[i] = sum / float64(count)
		}
	}
	return []*types.PerfTrace{ret}, nil
}

func (AveFunc) Describe() string {
	return `ave() averages the values of all argument traces into a single trace.`
}

var aveFunc = AveFunc{}

type RatioFunc struct{}

func (RatioFunc) Eval(ctx *Context, node *Node) ([]*types.PerfTrace, error) {
	if len(node.Args) != 2 {
		return nil, fmt.Errorf("ratio() takes two arguments")
	}

	tracesA, err := node.Args[0].Eval(ctx)
	if err != nil {
		return nil, fmt.Errorf("ratio() argument failed to evaluate: %s", err)
	}

	tracesB, err := node.Args[1].Eval(ctx)
	if err != nil {
		return nil, fmt.Errorf("ratio() argument failed to evaluate: %s", err)
	}

	ret := types.NewPerfTraceN(len(tracesA[0].Values))
	for i, _ := range ret.Values {
		ret.Values[i] = tracesA[0].Values[i] / tracesB[0].Values[i]
		if math.IsInf(ret.Values[i], 0) {
			ret.Values[i] = config.MISSING_DATA_SENTINEL
		}
	}
	return []*types.PerfTrace{ret}, nil
}

func (RatioFunc) Describe() string {
	return `ratio(a, b) returns the point by point ratio of two traces.
                That is, it returns a trace with a[i]/b[i] for every point in a and b.`
}

var ratioFunc = RatioFunc{}

// CountFunc implements Func and counts the number of non-sentinel values in
// all argument traces.
//
// MISSING_DATA_SENTINEL values are not included in the count.  Note that if
// all the values at an index are MISSING_DATA_SENTINEL then the count will
// be 0.
type CountFunc struct{}

func (CountFunc) Eval(ctx *Context, node *Node) ([]*types.PerfTrace, error) {
	if len(node.Args) != 1 {
		return nil, fmt.Errorf("count() takes a single argument.")
	}
	if node.Args[0].Typ != NodeFunc {
		return nil, fmt.Errorf("count() takes a function argument.")
	}
	traces, err := node.Args[0].Eval(ctx)
	if err != nil {
		return nil, fmt.Errorf("count() argument failed to evaluate: %s", err)
	}

	if len(traces) == 0 {
		return traces, nil
	}

	ret := types.NewPerfTraceN(len(traces[0].Values))
	ret.Params()["id"] = types.AsFormulaID(ctx.formula)
	for i, _ := range ret.Values {
		count := 0
		for _, tr := range traces {
			if v := tr.Values[i]; v != config.MISSING_DATA_SENTINEL {
				count += 1
			}
		}
		ret.Values[i] = float64(count)
	}
	return []*types.PerfTrace{ret}, nil
}

func (CountFunc) Describe() string {
	return `count() counts the non-missing values of all argument traces.`
}

var countFunc = CountFunc{}

type SumFunc struct{}

// SumFunc implements Func and sums the values of all argument
// traces into a single trace.
//
// MISSING_DATA_SENTINEL values are not included in the sum. Note that if all
// the values at an index are MISSING_DATA_SENTINEL then the sum will be
// MISSING_DATA_SENTINEL.
func (SumFunc) Eval(ctx *Context, node *Node) ([]*types.PerfTrace, error) {
	if len(node.Args) != 1 {
		return nil, fmt.Errorf("Sum() takes a single argument.")
	}
	if node.Args[0].Typ != NodeFunc {
		return nil, fmt.Errorf("Sum() takes a function argument.")
	}
	traces, err := node.Args[0].Eval(ctx)
	if err != nil {
		return nil, fmt.Errorf("Sum() argument failed to evaluate: %s", err)
	}

	if len(traces) == 0 {
		return traces, nil
	}

	ret := types.NewPerfTraceN(len(traces[0].Values))
	ret.Params()["id"] = types.AsFormulaID(ctx.formula)
	for i, _ := range ret.Values {
		sum := 0.0
		count := 0
		for _, tr := range traces {
			if v := tr.Values[i]; v != config.MISSING_DATA_SENTINEL {
				sum += v
				count += 1
			}
		}
		if count > 0 {
			ret.Values[i] = sum
		}
	}
	return []*types.PerfTrace{ret}, nil
}

func (SumFunc) Describe() string {
	return `Sum() Sums the values of all argument traces into a single trace.`
}

var sumFunc = SumFunc{}

type GeoFunc struct{}

// geoFunc implements Func and merges the values of all argument
// traces into a single trace with a geometric mean.
//
// MISSING_DATA_SENTINEL and negative values are not included in the mean.
// Note that if all the values at an index are MISSING_DATA_SENTINEL or
// negative then the mean will be MISSING_DATA_SENTINEL.
func (GeoFunc) Eval(ctx *Context, node *Node) ([]*types.PerfTrace, error) {
	if len(node.Args) != 1 {
		return nil, fmt.Errorf("geo() takes a single argument.")
	}
	if node.Args[0].Typ != NodeFunc {
		return nil, fmt.Errorf("geo() takes a function argument.")
	}
	traces, err := node.Args[0].Eval(ctx)
	if err != nil {
		return nil, fmt.Errorf("geo() argument failed to evaluate: %s", err)
	}

	if len(traces) == 0 {
		return traces, nil
	}

	ret := types.NewPerfTraceN(len(traces[0].Values))
	ret.Params()["id"] = types.AsFormulaID(ctx.formula)
	for i, _ := range ret.Values {
		// We're accumulating a product, but in log-space to avoid large N overflow.
		sumLog := 0.0
		count := 0
		for _, tr := range traces {
			if v := tr.Values[i]; v >= 0 && v != config.MISSING_DATA_SENTINEL {
				sumLog += math.Log(v)
				count += 1
			}
		}
		if count > 0 {
			// The geometric mean is the N-th root of the product of N terms.
			// In log-space, the root becomes a division, then we translate back to normal space.
			ret.Values[i] = math.Exp(sumLog / float64(count))
		}
	}
	return []*types.PerfTrace{ret}, nil
}

func (GeoFunc) Describe() string {
	return `geo() folds the values of all argument traces into a single geometric mean trace.`
}

var geoFunc = GeoFunc{}
