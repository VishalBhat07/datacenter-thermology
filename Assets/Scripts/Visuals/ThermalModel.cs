using System;
using System.Collections.Generic;
using UnityEngine;

namespace DataCenterGuard
{
    internal sealed class ThermalModel
    {
        internal const int Normal = 1;
        internal const int LoadSpike = 2;
        internal const int CoolingOutage = 3;
        internal const float StepSeconds = 0.1f;
        internal const int WarmupSteps = 9000;

        private const float SupplyTemperature = 18f;
        private const float VolumetricHeatCapacity = 1206f; // J/(m³·K)
        private const float EffectiveDepth = 1f;
        private const float RackAirflow = 0.24f; // m³/s per rack
        private const float MixingDiffusivity = 0.12f; // m²/s
        private const float CoolingCapacity = 12000f; // W per CRAC

        private readonly RackLoop[] racks;
        private readonly float[] demand;
        private readonly float[] coolingFraction;
        private float[] temperature = new float[ThermalHeatmap.Width * ThermalHeatmap.Depth];
        private float[] next = new float[ThermalHeatmap.Width * ThermalHeatmap.Depth];

        internal int OutageUnit { get; }
        internal string SpikeRack { get; }
        internal float HeatingWatts { get; private set; }
        internal float CoolingWatts { get; private set; }

        private struct RackLoop
        {
            internal int Inlet;
            internal int Exhaust;
            internal int Cooler;
            internal RackLocation Location;
        }

        internal ThermalModel(List<Transform> rackTransforms, List<Transform> coolingUnits)
        {
            racks = new RackLoop[rackTransforms.Count];
            demand = new float[coolingUnits.Count];
            coolingFraction = new float[coolingUnits.Count];
            var served = new int[coolingUnits.Count];
            for (int i = 0; i < racks.Length; i++)
            {
                Transform rack = rackTransforms[i];
                Vector3 inlet = rack.position + rack.forward * 1.05f;
                Vector3 exhaust = rack.position - rack.forward * 1.05f;
                int cooler = 0;
                float nearest = float.MaxValue;
                for (int j = 0; j < coolingUnits.Count; j++)
                {
                    float distance = (coolingUnits[j].position - inlet).sqrMagnitude;
                    if (distance < nearest)
                    {
                        nearest = distance;
                        cooler = j;
                    }
                }

                served[cooler]++;
                int inletCell = Cell(inlet);
                Vector2 inletCenter = ThermalHeatmap.CellCenter(
                    inletCell % ThermalHeatmap.Width, inletCell / ThermalHeatmap.Width);
                racks[i] = new RackLoop
                {
                    Inlet = inletCell,
                    Exhaust = Cell(exhaust),
                    Cooler = cooler,
                    Location = new RackLocation(rack.position, rack.eulerAngles.y, inletCenter)
                };
            }

            for (int i = 1; i < served.Length; i++)
                if (served[i] > served[OutageUnit])
                    OutageUnit = i;

            int spikeIndex = racks.Length / 2;
            SpikeRack = $"{(char)('A' + spikeIndex / 8)}{spikeIndex % 8 + 1:00}";
            Array.Fill(temperature, 22f);
        }

        private ThermalModel(ThermalModel source)
        {
            racks = source.racks;
            demand = new float[source.demand.Length];
            coolingFraction = new float[source.coolingFraction.Length];
            Array.Copy(source.temperature, temperature, temperature.Length);
            OutageUnit = source.OutageUnit;
            SpikeRack = source.SpikeRack;
            HeatingWatts = source.HeatingWatts;
            CoolingWatts = source.CoolingWatts;
        }

        internal ThermalModel Fork() => new ThermalModel(this);
        internal float TemperatureAt(int cell) => temperature[cell];
        internal float InletAt(int slot) => temperature[racks[slot].Inlet];
        internal RackLocation LocationAt(int slot) => racks[slot].Location;
        internal int CoolerFor(int slot) => racks[slot].Cooler;
        internal void CopyField(float[] buffer) => Array.Copy(temperature, buffer, temperature.Length);

        internal void Advance(int steps, int scenario, float rackWatts, float spikeWatts)
        {
            int heatedSlot = scenario == LoadSpike ? racks.Length / 2 : -1;
            int offlineCooler = scenario == CoolingOutage ? OutageUnit : -1;
            Advance(steps, rackWatts, spikeWatts, heatedSlot, offlineCooler);
        }

        internal void Advance(int steps, float rackWatts, float spikeWatts, int heatedSlot, int offlineCooler)
        {
            for (int i = 0; i < steps; i++)
                Step(rackWatts, spikeWatts, heatedSlot, offlineCooler);
        }

        internal ThermalSnapshot Snapshot()
        {
            int peak = 0;
            double sum = 0;
            for (int i = 0; i < temperature.Length; i++)
            {
                sum += temperature[i];
                if (temperature[i] > temperature[peak])
                    peak = i;
            }

            float peakInlet = float.MinValue;
            foreach (RackLoop rack in racks)
                peakInlet = Mathf.Max(peakInlet, temperature[rack.Inlet]);

            Vector2 point = ThermalHeatmap.CellCenter(peak % ThermalHeatmap.Width, peak / ThermalHeatmap.Width);
            return new ThermalSnapshot
            {
                PeakC = temperature[peak],
                PeakInletC = peakInlet,
                MeanC = (float)(sum / temperature.Length),
                PeakX = point.x,
                PeakZ = point.y,
                HeatingKw = HeatingWatts / 1000f,
                CoolingKw = CoolingWatts / 1000f
            };
        }

        private void Step(float rackWatts, float spikeWatts, int heatedSlot, int offlineCooler)
        {
            const int width = ThermalHeatmap.Width;
            const int depth = ThermalHeatmap.Depth;
            const float spacing = ThermalHeatmap.CellSize;
            const float mixing = MixingDiffusivity * StepSeconds / (spacing * spacing);
            const float heatCapacity = VolumetricHeatCapacity * spacing * spacing * EffectiveDepth;
            const float airflowHeatCapacity = VolumetricHeatCapacity * RackAirflow;
            const float heatToTemperature = StepSeconds / heatCapacity;

            // No heat crosses the grid boundary. Each internal face transfers equal and opposite heat.
            for (int z = 0; z < depth; z++)
            for (int x = 0; x < width; x++)
            {
                int cell = z * width + x;
                float t = temperature[cell];
                float change = 0f;
                if (x > 0) change += temperature[cell - 1] - t;
                if (x < width - 1) change += temperature[cell + 1] - t;
                if (z > 0) change += temperature[cell - width] - t;
                if (z < depth - 1) change += temperature[cell + width] - t;
                next[cell] = t + mixing * change;
            }

            Array.Clear(demand, 0, demand.Length);
            foreach (RackLoop rack in racks)
                demand[rack.Cooler] += airflowHeatCapacity * Math.Max(0f, temperature[rack.Exhaust] - SupplyTemperature);

            for (int i = 0; i < demand.Length; i++)
                coolingFraction[i] = i == offlineCooler
                    ? 0f
                    : demand[i] > CoolingCapacity ? CoolingCapacity / demand[i] : 1f;

            HeatingWatts = 0f;
            CoolingWatts = 0f;
            for (int i = 0; i < racks.Length; i++)
            {
                RackLoop rack = racks[i];
                float inlet = temperature[rack.Inlet];
                float exhaust = temperature[rack.Exhaust];
                float sensibleTransfer = airflowHeatCapacity * (inlet - exhaust);
                float cooling = airflowHeatCapacity * Math.Max(0f, exhaust - SupplyTemperature) * coolingFraction[rack.Cooler];
                float heating = i == heatedSlot ? spikeWatts : rackWatts;

                // The exhaust receives inlet air and rack heat. Return air reaches the inlet after cooling.
                next[rack.Exhaust] += heatToTemperature * (sensibleTransfer + heating);
                next[rack.Inlet] -= heatToTemperature * (sensibleTransfer + cooling);
                HeatingWatts += heating;
                CoolingWatts += cooling;
            }

            (temperature, next) = (next, temperature);
        }

        internal readonly struct RackLocation
        {
            internal readonly Vector3 Position;
            internal readonly float Yaw;
            internal readonly Vector2 InletCenter;

            internal RackLocation(Vector3 position, float yaw, Vector2 inletCenter)
            {
                Position = position;
                Yaw = yaw;
                InletCenter = inletCenter;
            }
        }

        private static int Cell(Vector3 point)
        {
            int x = Mathf.Clamp(Mathf.FloorToInt((point.x - ThermalHeatmap.MinX) / ThermalHeatmap.CellSize), 0, ThermalHeatmap.Width - 1);
            int z = Mathf.Clamp(Mathf.FloorToInt((point.z - ThermalHeatmap.MinZ) / ThermalHeatmap.CellSize), 0, ThermalHeatmap.Depth - 1);
            return z * ThermalHeatmap.Width + x;
        }

    }

    public struct ThermalSnapshot
    {
        public float PeakC;
        public float PeakInletC;
        public float MeanC;
        public float PeakX;
        public float PeakZ;
        public float HeatingKw;
        public float CoolingKw;
    }

    public struct ThermalComparison
    {
        public ThermalSnapshot Normal;
        public ThermalSnapshot LoadSpike;
        public ThermalSnapshot CoolingOutage;
        public float OutageRecoverySeconds;
        public string OfflineUnit;
        public string SpikeRack;
    }
}
