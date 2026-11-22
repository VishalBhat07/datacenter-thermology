using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;

namespace DataCenterGuard
{
    internal static class RackLayoutAnalysis
    {
        internal const int DurationSeconds = 180;
        internal const float InletLimitC = 27f;
        internal const int SpikeWithAllCooling = 1;

        internal static Analysis Run(ThermalModel baseline, int slotCount, int coolerCount,
            int sourceSlot, float baseWatts, float highWatts, CancellationToken token)
        {
            var timer = Stopwatch.StartNew();
            var candidates = new Candidate[slotCount];
            baseline.Advance(ThermalModel.WarmupSteps, ThermalModel.Normal, baseWatts, highWatts);
            ScenarioMetrics normal = Simulate(baseline.Fork(), slotCount, baseWatts, highWatts, -1, -1, token);
            int[] servedRacks = new int[coolerCount];
            for (int slot = 0; slot < slotCount; slot++)
                servedRacks[baseline.CoolerFor(slot)]++;

            var result = new Analysis
            {
                Candidates = candidates,
                SourceSlot = sourceSlot,
                BaseWatts = baseWatts,
                HighWatts = highWatts,
                BalancedSlot = sourceSlot,
                NormalOnlySlot = sourceSlot,
                RecommendedSlot = sourceSlot
            };
            for (int slot = 0; slot < slotCount; slot++)
            {
                token.ThrowIfCancellationRequested();
                var cases = new ScenarioMetrics[coolerCount + 2];
                cases[0] = normal;
                for (int scenario = 1; scenario < cases.Length; scenario++)
                    cases[scenario] = Simulate(baseline.Fork(), slotCount, baseWatts, highWatts, slot, scenario - 2, token);
                candidates[slot] = new Candidate(cases);
            }

            for (int slot = 0; slot < slotCount; slot++)
            {
                Candidate candidate = candidates[slot];
                if (Better(candidate.Cases[SpikeWithAllCooling], candidates[result.NormalOnlySlot].Cases[SpikeWithAllCooling]))
                    result.NormalOnlySlot = slot;
                if (Better(candidate.Worst, candidates[result.RecommendedSlot].Worst))
                    result.RecommendedSlot = slot;
                if (servedRacks[baseline.CoolerFor(slot)] < servedRacks[baseline.CoolerFor(result.BalancedSlot)])
                    result.BalancedSlot = slot;
            }

            result.RecordTraces(baseline, coolerCount + 2, token);
            result.ElapsedSeconds = timer.Elapsed.TotalSeconds;
            return result;
        }

        private static ScenarioMetrics Simulate(ThermalModel model, int slotCount, float baseWatts,
            float highWatts, int hotSlot, int offlineCooler, CancellationToken token, float[] trace = null)
        {
            var result = new ScenarioMetrics();
            var exceeded = new bool[slotCount];
            for (int second = 0; second <= DurationSeconds; second++)
            {
                if (second > 0)
                {
                    token.ThrowIfCancellationRequested();
                    model.Advance(10, baseWatts, highWatts, hotSlot, offlineCooler);
                }

                float peakThisSecond = float.NegativeInfinity;
                int hottestSlot = 0;
                for (int slot = 0; slot < slotCount; slot++)
                {
                    float inlet = model.InletAt(slot);
                    if (trace != null)
                        trace[second * slotCount + slot] = inlet;

                    if (inlet > peakThisSecond)
                    {
                        peakThisSecond = inlet;
                        hottestSlot = slot;
                    }

                    if (inlet > InletLimitC)
                    {
                        exceeded[slot] = true;
                        // Integrate the sampled exceedance with half weights at the interval ends.
                        float sampleWeight = second == 0 || second == DurationSeconds ? 0.5f : 1f;
                        result.DegreeMinutes += sampleWeight * (inlet - InletLimitC) / 60f;
                    }
                }

                if (peakThisSecond > result.Peak)
                {
                    result.Peak = peakThisSecond;
                    result.PeakSecond = second;
                    result.HottestSlot = hottestSlot;
                    model.CopyField(result.Field);
                }
            }

            foreach (bool overLimit in exceeded)
                if (overLimit)
                    result.RacksOverLimit++;

            return result;
        }

        private static int CompareRisk(ScenarioMetrics a, ScenarioMetrics b)
        {
            int peak = Math.Round(a.Peak, 2).CompareTo(Math.Round(b.Peak, 2));
            return peak != 0 ? peak : Math.Round(a.DegreeMinutes, 2).CompareTo(Math.Round(b.DegreeMinutes, 2));
        }

        private static bool Better(ScenarioMetrics candidate, ScenarioMetrics incumbent) => CompareRisk(candidate, incumbent) < 0;

        internal static string SlotName(int slot) => $"{(char)('A' + slot / 8)}{slot % 8 + 1:00}";

        internal sealed class ScenarioMetrics
        {
            internal readonly float[] Field = new float[ThermalHeatmap.Width * ThermalHeatmap.Depth];
            internal float Peak = float.NegativeInfinity;
            internal float DegreeMinutes;
            internal int RacksOverLimit;
            internal int HottestSlot;
            internal int PeakSecond;
        }

        internal sealed class Candidate
        {
            internal readonly ScenarioMetrics[] Cases;
            internal readonly int WorstIndex;
            internal ScenarioMetrics Worst => Cases[WorstIndex];

            internal Candidate(ScenarioMetrics[] cases)
            {
                Cases = cases;
                for (int i = 1; i < cases.Length; i++)
                    if (Better(cases[WorstIndex], cases[i]))
                        WorstIndex = i;
            }
        }

        internal sealed class Analysis
        {
            internal Candidate[] Candidates;
            internal int SourceSlot;
            internal int NormalOnlySlot;
            internal int BalancedSlot;
            internal int RecommendedSlot;
            internal float BaseWatts;
            internal float HighWatts;
            internal double ElapsedSeconds;

            private static readonly string[] TraceRoles = { "current", "all_cooling", "outage_aware" };
            private int[] traceSlots;
            private float[][][] traces;
            private ThermalModel.RackLocation[] locations;

            internal void RecordTraces(ThermalModel baseline, int caseCount, CancellationToken token)
            {
                int rackCount = Candidates.Length;
                traceSlots = new[] { SourceSlot, NormalOnlySlot, RecommendedSlot };
                traces = new float[traceSlots.Length][][];
                locations = new ThermalModel.RackLocation[rackCount];
                for (int rack = 0; rack < rackCount; rack++)
                    locations[rack] = baseline.LocationAt(rack);

                for (int role = 0; role < traceSlots.Length; role++)
                {
                    int earlierRole = Array.IndexOf(traceSlots, traceSlots[role]);
                    if (earlierRole < role)
                    {
                        traces[role] = traces[earlierRole];
                        continue;
                    }

                    traces[role] = new float[caseCount][];
                    for (int scenario = 0; scenario < caseCount; scenario++)
                    {
                        if (scenario == 0 && role > 0)
                        {
                            traces[role][scenario] = traces[0][scenario];
                            continue;
                        }

                        var trace = new float[(DurationSeconds + 1) * rackCount];
                        traces[role][scenario] = trace;
                        int heatedSlot = scenario == 0 ? -1 : traceSlots[role];
                        Simulate(baseline.Fork(), rackCount, BaseWatts, HighWatts, heatedSlot,
                            Math.Max(-1, scenario - 2), token, trace);
                    }
                }
            }

            internal void WriteTraces(TextWriter writer, string[] scenarioNames)
            {
                writer.WriteLine("layout,peak_load_slot,scenario,offline_crac,time_s,rack_slot,rack_x_m,rack_y_m,rack_z_m,rack_yaw_deg,inlet_x_m,inlet_y_m,inlet_z_m,base_rack_w,peak_rack_w,rack_power_w,inlet_c,limit_c");
                int rackCount = locations.Length;
                var rackNames = new string[rackCount];
                for (int rack = 0; rack < rackCount; rack++)
                    rackNames[rack] = SlotName(rack);

                for (int role = 0; role < TraceRoles.Length; role++)
                for (int scenario = 0; scenario < scenarioNames.Length; scenario++)
                {
                    int hotSlot = traceSlots[role];
                    string offlineCrac = scenario >= 2 ? scenarioNames[scenario] : "";
                    string scenarioName = scenario >= 2 ? "CRAC outage" : scenarioNames[scenario];
                    float[] readings = traces[role][scenario];
                    for (int second = 0; second <= DurationSeconds; second++)
                    for (int rack = 0; rack < rackCount; rack++)
                    {
                        ThermalModel.RackLocation location = locations[rack];
                        float power = scenario > 0 && rack == hotSlot ? HighWatts : BaseWatts;
                        float inlet = readings[second * rackCount + rack];
                        writer.WriteLine(string.Format(CultureInfo.InvariantCulture,
                            "{0},{1},{2},{3},{4},{5},{6:F3},{7:F3},{8:F3},{9:F1}," +
                            "{10:F3},{11:F3},{12:F3},{13:F1},{14:F1},{15:F1},{16:F4},{17:F1}",
                            TraceRoles[role], rackNames[hotSlot], scenarioName, offlineCrac, second, rackNames[rack],
                            location.Position.x, location.Position.y, location.Position.z, location.Yaw,
                            location.InletCenter.x, ThermalHeatmap.SliceHeight, location.InletCenter.y,
                            BaseWatts, HighWatts, power, inlet, InletLimitC));
                    }
                }
            }

            internal string ToCsv(string[] scenarioNames)
            {
                var csv = new StringBuilder("source_slot,high_load_slot,scenario,duration_s,base_rack_w,high_rack_w,scenario_it_w,limit_c,worst_inlet_c,hottest_slot,peak_time_s,racks_exceeding_limit,rack_degree_minutes,baseline,load_balance,normal_only,outage_aware,analysis_wall_time_s\n");
                for (int slot = 0; slot < Candidates.Length; slot++)
                for (int scenario = 0; scenario < scenarioNames.Length; scenario++)
                {
                    ScenarioMetrics metric = Candidates[slot].Cases[scenario];
                    csv.AppendFormat(CultureInfo.InvariantCulture,
                        "{0},{1},{2},{3},{4:F1},{5:F1},{6:F1},{7:F1},{8:F4},{9},{10},{11},{12:F4},{13},{14},{15},{16},{17:F3}\n",
                        SlotName(SourceSlot), SlotName(slot), scenarioNames[scenario], DurationSeconds,
                        BaseWatts, HighWatts, BaseWatts * (Candidates.Length - 1) + (scenario == 0 ? BaseWatts : HighWatts), InletLimitC,
                        metric.Peak, SlotName(metric.HottestSlot), metric.PeakSecond, metric.RacksOverLimit,
                        metric.DegreeMinutes, slot == SourceSlot, slot == BalancedSlot,
                        slot == NormalOnlySlot, slot == RecommendedSlot, ElapsedSeconds);
                }

                return csv.ToString();
            }
        }
    }
}
