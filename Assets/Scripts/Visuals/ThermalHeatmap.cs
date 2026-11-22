using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.SceneManagement;

namespace DataCenterGuard
{
    public sealed class ThermalHeatmap : MonoBehaviour
    {
        internal const int Width = 22;
        internal const int Depth = 20;
        internal const float CellSize = 0.6f;
        internal const float MinX = -6.6f;
        internal const float MinZ = -7.8f;
        internal const float SliceHeight = 1.15f;
        private const float MinTemperature = 18f;
        private const float MaxTemperature = 42f;
        private const float DrawInterval = 0.1f;

        private static readonly string[] Modes = { "Demo", "Normal", "Load spike", "CRAC outage" };
        private const float SimulationSpeed = 12f;
        private readonly List<Transform> racks = new List<Transform>();
        private readonly List<Transform> coolers = new List<Transform>();
        private readonly List<Vector3> exhausts = new List<Vector3>();
        private readonly Color32[] pixels = new Color32[Width * Depth];
        private Texture2D texture;
        private Texture2D legend;
        private Material material;
        private Mesh mesh;
        private MeshRenderer overlay;
        private float nextDraw;
        private ThermalModel model;
        private int scenario;
        private float simulationTime;
        private float stepBudget;
        private float rackLoadKw = 1f;
        private float spikeLoadKw = 4f;
        private ThermalSnapshot current;
        private ThermalComparison? comparison;
        private bool showLayoutAnalysis;
        private bool previewSwap;
        private int sourceSlot = 16;
        private int previewCase;
        private string[] analysisCases;
        private CancellationTokenSource analysisCancellation;
        private Task<RackLayoutAnalysis.Analysis> analysisTask;
        private Task<string> traceExportTask;
        private RackLayoutAnalysis.Analysis layoutResult;
        private string analysisMessage;
        private Vector2 analysisScroll;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Subscribe()
        {
            SceneManager.sceneLoaded -= AddToDataHall;
            SceneManager.sceneLoaded += AddToDataHall;
            AddToDataHall(SceneManager.GetActiveScene(), LoadSceneMode.Single);
        }

        private static void AddToDataHall(Scene scene, LoadSceneMode mode)
        {
            if (!Application.isPlaying || scene.path != "Assets/Scenes/DataCenterGuard.unity")
                return;

            var root = new GameObject("ThermoWatch Heatmap");
            SceneManager.MoveGameObjectToScene(root, scene);
            root.AddComponent<ThermalHeatmap>();
        }

        private void Awake()
        {
            foreach (GameObject root in gameObject.scene.GetRootGameObjects())
            {
                if (root.name == "Server Racks")
                {
                    foreach (Transform rack in root.transform)
                    {
                        racks.Add(rack);
                        exhausts.Add(rack.position - rack.forward * 1.05f);
                    }
                }
                else if (root.name == "Cooling Stations")
                {
                    foreach (Transform unit in root.transform)
                        coolers.Add(unit);
                }
            }

            racks.Sort((a, b) => a.position.z != b.position.z
                ? a.position.z.CompareTo(b.position.z)
                : a.position.x.CompareTo(b.position.x));
            coolers.Sort((a, b) => string.CompareOrdinal(a.name, b.name));
            if (racks.Count != 32 || coolers.Count != 4)
            {
                Debug.LogError($"ThermoWatch expects 32 racks and 4 cooling units, found {racks.Count} and {coolers.Count}.");
                enabled = false;
                return;
            }

            analysisCases = new string[coolers.Count + 2];
            analysisCases[0] = "Normal";
            analysisCases[1] = "Peak load";
            for (int i = 0; i < coolers.Count; i++)
                analysisCases[i + 2] = coolers[i].name;

            Shader shader = Resources.Load<Shader>("ThermalSlice");
            if (shader == null)
            {
                Debug.LogError("ThermoWatch could not load its thermal slice shader.");
                enabled = false;
                return;
            }

            texture = new Texture2D(Width, Depth, TextureFormat.RGBA32, false)
            {
                wrapMode = TextureWrapMode.Clamp,
                filterMode = FilterMode.Bilinear
            };
            material = new Material(shader) { mainTexture = texture };
            mesh = new Mesh { name = "Thermal slice" };
            float maxX = MinX + Width * CellSize;
            float maxZ = MinZ + Depth * CellSize;
            mesh.vertices = new[]
            {
                new Vector3(MinX, SliceHeight, MinZ), new Vector3(maxX, SliceHeight, MinZ),
                new Vector3(MinX, SliceHeight, maxZ), new Vector3(maxX, SliceHeight, maxZ)
            };
            mesh.uv = new[] { Vector2.zero, Vector2.right, Vector2.up, Vector2.one };
            mesh.triangles = new[] { 0, 2, 1, 1, 2, 3 };
            gameObject.AddComponent<MeshFilter>().sharedMesh = mesh;
            overlay = gameObject.AddComponent<MeshRenderer>();
            overlay.sharedMaterial = material;

            legend = new Texture2D(128, 1, TextureFormat.RGBA32, false);
            for (int x = 0; x < legend.width; x++)
                legend.SetPixel(x, 0, Ramp(x / (legend.width - 1f), 1f));
            legend.wrapMode = TextureWrapMode.Clamp;
            legend.Apply();

            DrawField(0f);
        }

        private void Update()
        {
            if (Keyboard.current?.hKey.wasPressedThisFrame == true)
                ToggleHeatmap();

            PollLayoutAnalysis();
            PollTraceExport();
            if (showLayoutAnalysis && layoutResult != null)
                return;

            if (scenario != 0)
            {
                stepBudget += Mathf.Min(Time.deltaTime, 0.1f) * SimulationSpeed;
                int steps = (int)(stepBudget / ThermalModel.StepSeconds);
                model.Advance(steps, scenario, rackLoadKw * 1000f, spikeLoadKw * 1000f);
                float elapsed = steps * ThermalModel.StepSeconds;
                stepBudget -= elapsed;
                simulationTime += elapsed;
            }

            if (overlay.enabled && Time.unscaledTime >= nextDraw)
            {
                DrawField(Time.timeSinceLevelLoad);
                nextDraw = Time.unscaledTime + DrawInterval;
            }
        }

        public void ToggleHeatmap()
        {
            overlay.enabled = !overlay.enabled;
        }

        private void DrawField(float seconds)
        {
            for (int z = 0; z < Depth; z++)
            for (int x = 0; x < Width; x++)
            {
                int cell = z * Width + x;
                float temperature = scenario == 0 ? DemoTemperature(x, z, seconds) : model.TemperatureAt(cell);
                pixels[cell] = Ramp(Mathf.InverseLerp(MinTemperature, MaxTemperature, temperature), 0.66f);
            }

            ApplyPixels();
            if (scenario != 0)
                current = model.Snapshot();
        }

        private void ApplyPixels()
        {
            texture.SetPixels32(pixels);
            texture.Apply();
        }

        private float DemoTemperature(int x, int z, float seconds)
        {
            Vector2 position = CellCenter(x, z);
            float coldDistance = Mathf.Min(Mathf.Abs(position.y + 4.2f), Mathf.Abs(position.y - 0.6f));
            float temperature = 23f - 4f * Mathf.Exp(-coldDistance * coldDistance * 2.5f);
            foreach (Vector3 exhaust in exhausts)
            {
                float dx = position.x - exhaust.x;
                float dz = position.y - exhaust.z;
                float load = 5.5f + 2.5f * Mathf.Sin(seconds * 1.4f + exhaust.x + exhaust.z);
                temperature += load * Mathf.Exp(-dx * dx * 2f - dz * dz * 2.6f);
            }

            return temperature;
        }

        public void SelectScenario(int mode)
        {
            if (mode < 0 || mode >= Modes.Length)
                throw new ArgumentOutOfRangeException(nameof(mode));

            if (mode != 0 && (model == null || scenario == 0))
                ResetModel();

            scenario = mode;
            simulationTime = 0f;
            DrawField(Time.timeSinceLevelLoad);
            nextDraw = Time.unscaledTime + DrawInterval;
        }

        private ThermalModel NewBaseline()
        {
            var baseline = new ThermalModel(racks, coolers);
            baseline.Advance(ThermalModel.WarmupSteps, ThermalModel.Normal, rackLoadKw * 1000f, spikeLoadKw * 1000f);
            return baseline;
        }

        private void ResetModel()
        {
            model = NewBaseline();
            simulationTime = 0f;
            stepBudget = 0f;
            nextDraw = 0f;
        }

        public ThermalComparison CompareScenarios()
        {
            const int scenarioSteps = 1800;
            const int maxRecoverySeconds = 900;
            const float recoveryMargin = 0.5f;
            float rackWatts = rackLoadKw * 1000f;
            float spikeWatts = spikeLoadKw * 1000f;
            ThermalModel normal = NewBaseline();
            ThermalModel spike = NewBaseline();
            ThermalModel outage = NewBaseline();
            normal.Advance(scenarioSteps, ThermalModel.Normal, rackWatts, spikeWatts);
            spike.Advance(scenarioSteps, ThermalModel.LoadSpike, rackWatts, spikeWatts);
            outage.Advance(scenarioSteps, ThermalModel.CoolingOutage, rackWatts, spikeWatts);
            ThermalSnapshot baseline = normal.Snapshot();
            ThermalSnapshot outageEnd = outage.Snapshot();
            float recovery = -1f;
            for (int seconds = 0; seconds <= maxRecoverySeconds; seconds++)
            {
                ThermalSnapshot snapshot = outage.Snapshot();
                if (snapshot.PeakC <= baseline.PeakC + recoveryMargin &&
                    snapshot.PeakInletC <= baseline.PeakInletC + recoveryMargin)
                {
                    recovery = seconds;
                    break;
                }
                outage.Advance(10, ThermalModel.Normal, rackWatts, spikeWatts);
            }

            return new ThermalComparison
            {
                Normal = baseline,
                LoadSpike = spike.Snapshot(),
                CoolingOutage = outageEnd,
                OutageRecoverySeconds = recovery,
                OfflineUnit = coolers[outage.OutageUnit].name,
                SpikeRack = spike.SpikeRack
            };
        }

        internal static Vector2 CellCenter(int x, int z)
        {
            return new Vector2(MinX + (x + 0.5f) * CellSize, MinZ + (z + 0.5f) * CellSize);
        }

        private static Color Ramp(float t, float alpha)
        {
            Color cold = new Color(0.05f, 0.37f, 0.96f);
            Color warm = new Color(1f, 0.77f, 0.14f);
            Color hot = new Color(0.83f, 0.09f, 0.13f);
            Color color = t < 0.5f
                ? Color.Lerp(cold, warm, t * 2f)
                : Color.Lerp(warm, hot, (t - 0.5f) * 2f);
            color.a = alpha;
            return color;
        }

        private void OnGUI()
        {
            Matrix4x4 previous = GUI.matrix;
            float scale = Mathf.Clamp(Screen.width / 1440f, 1f, 2.5f);
            GUI.matrix = previous * Matrix4x4.Scale(new Vector3(scale, scale, 1f));
            DrawControls();
            GUI.matrix = previous;
        }

        public void StartLayoutAnalysis()
        {
            if (analysisTask != null)
                return;

            showLayoutAnalysis = true;
            layoutResult = null;
            analysisMessage = null;
            int slot = sourceSlot;
            int slotCount = racks.Count;
            int coolerCount = coolers.Count;
            float baseWatts = rackLoadKw * 1000f;
            float highWatts = spikeLoadKw * 1000f;
            var baseline = new ThermalModel(racks, coolers);
            analysisCancellation = new CancellationTokenSource();
            CancellationToken token = analysisCancellation.Token;
            analysisTask = Task.Run(() => RackLayoutAnalysis.Run(
                baseline, slotCount, coolerCount, slot, baseWatts, highWatts, token), token);
        }

        private void PollLayoutAnalysis()
        {
            if (analysisTask == null || !analysisTask.IsCompleted)
                return;

            if (analysisTask.Status == TaskStatus.RanToCompletion)
            {
                layoutResult = analysisTask.Result;
                previewSwap = true;
                previewCase = layoutResult.Candidates[layoutResult.SourceSlot].WorstIndex;
                if (showLayoutAnalysis)
                    DrawLayoutField();
            }
            else if (analysisTask.IsFaulted)
            {
                analysisMessage = "Layout analysis failed. See the Unity Console.";
                Debug.LogException(analysisTask.Exception.GetBaseException());
            }

            analysisCancellation.Dispose();
            analysisCancellation = null;
            analysisTask = null;
        }

        private void DrawLayoutField()
        {
            int slot = previewSwap ? layoutResult.RecommendedSlot : layoutResult.SourceSlot;
            float[] field = layoutResult.Candidates[slot].Cases[previewCase].Field;
            for (int i = 0; i < field.Length; i++)
                pixels[i] = Ramp(Mathf.InverseLerp(MinTemperature, MaxTemperature, field[i]), 0.66f);
            ApplyPixels();
        }

        private void DrawControls()
        {
            if (GUI.Button(new Rect(18, 18, 142, 34), overlay.enabled ? "Hide heatmap  [H]" : "Show heatmap  [H]"))
                ToggleHeatmap();

            if (GUI.Button(new Rect(168, 18, 142, 34), showLayoutAnalysis ? "Live heatmap" : "Analyze layout"))
            {
                showLayoutAnalysis = !showLayoutAnalysis;
                if (showLayoutAnalysis && layoutResult != null)
                    DrawLayoutField();
                else
                    DrawField(Time.timeSinceLevelLoad);
            }

            if (!overlay.enabled)
                return;

            if (showLayoutAnalysis)
            {
                DrawLayoutControls();
                return;
            }

            float height = scenario == 0 ? 104f : comparison.HasValue ? 285f : 211f;
            BeginPanel(new Rect(18, 60, 360, height));
            int mode = GUILayout.Toolbar(scenario, Modes);
            if (mode != scenario)
                SelectScenario(mode);

            GUILayout.Label(scenario == 0 ? "Demo temperatures · 1.15 m slice" : "Modeled air temperature · 1.15 m slice");
            DrawLegend();

            if (scenario != 0)
            {
                GUILayout.Label($"Peak {current.PeakC:F1} °C   ·   Warmest inlet {current.PeakInletC:F1} °C   ·   {simulationTime:F0} s");
                GUILayout.Label($"Heat added {current.HeatingKw:F1} kW   ·   Removed {current.CoolingKw:F1} kW");
                GUILayout.BeginHorizontal();
                GUILayout.Label($"Rack load {rackLoadKw:F1} kW", GUILayout.Width(112));
                GUI.enabled = analysisTask == null;
                float load = Mathf.Round(GUILayout.HorizontalSlider(rackLoadKw, 0.5f, 1.5f) * 10f) / 10f;
                GUILayout.Label($"Spike {spikeLoadKw:F1} kW", GUILayout.Width(87));
                float spike = Mathf.Round(GUILayout.HorizontalSlider(spikeLoadKw, 2f, 6f) * 10f) / 10f;
                GUI.enabled = true;
                GUILayout.EndHorizontal();
                if (load != rackLoadKw || spike != spikeLoadKw)
                {
                    comparison = null;
                    layoutResult = null;
                }
                rackLoadKw = load;
                spikeLoadKw = spike;

                string annotation = scenario == ThermalModel.CoolingOutage
                    ? $"{coolers[model.OutageUnit].name} offline"
                    : scenario == ThermalModel.LoadSpike ? $"Increased load at rack {model.SpikeRack}" : "All cooling units active";
                GUILayout.Label(annotation);
                GUILayout.BeginHorizontal();
                if (GUILayout.Button("Reset temperatures"))
                {
                    SelectScenario(ThermalModel.Normal);
                    ResetModel();
                }
                if (GUILayout.Button("Compare at 180 s"))
                    comparison = CompareScenarios();
                GUILayout.EndHorizontal();

                if (comparison.HasValue)
                {
                    ThermalComparison result = comparison.Value;
                    GUILayout.Label("Peak air temperature after 180 s");
                    GUILayout.Label($"Normal {result.Normal.PeakC:F1} °C   ·   Spike {result.LoadSpike.PeakC:F1} °C   ·   Outage {result.CoolingOutage.PeakC:F1} °C");
                    string recovery = result.OutageRecoverySeconds >= 0f ? $"{result.OutageRecoverySeconds:F0} s" : ">900 s";
                    GUILayout.Label($"{result.OfflineUnit} outage · Recovery {recovery}");
                }
            }

            GUILayout.EndArea();
        }

        private static void BeginPanel(Rect panel)
        {
            Color previous = GUI.color;
            GUI.color = new Color(0.07f, 0.10f, 0.15f, 0.96f);
            GUI.DrawTexture(panel, Texture2D.whiteTexture);
            GUI.color = previous;
            GUILayout.BeginArea(panel, GUI.skin.box);
        }

        private void DrawLegend()
        {
            GUILayout.Label(GUIContent.none, GUILayout.Height(11));
            GUI.DrawTexture(GUILayoutUtility.GetLastRect(), legend);
            GUILayout.BeginHorizontal();
            GUILayout.Label("≤18 °C");
            GUILayout.FlexibleSpace();
            GUILayout.Label("30 °C");
            GUILayout.FlexibleSpace();
            GUILayout.Label("≥42 °C");
            GUILayout.EndHorizontal();
        }

        private void DrawLayoutControls()
        {
            float scale = GUI.matrix.lossyScale.x;
            float width = Mathf.Min(456f, Screen.width / scale - 36f);
            float height = Mathf.Min(layoutResult == null ? 152f : 457f, Screen.height / scale - 70f);
            BeginPanel(new Rect(18, 60, width, height));
            analysisScroll = GUILayout.BeginScrollView(analysisScroll, false, false, GUIStyle.none, GUI.skin.verticalScrollbar);
            GUILayout.Label("Rack placement analysis");
            GUILayout.Label($"{rackLoadKw:F1} kW per rack   ·   {spikeLoadKw:F1} kW peak rack   ·   180 s per case");

            GUILayout.BeginHorizontal();
            GUI.enabled = analysisTask == null;
            if (GUILayout.Button("‹", GUILayout.Width(28)))
                ChangeSourceSlot(-1);
            GUILayout.Label($"Peak-load slot  {RackLayoutAnalysis.SlotName(sourceSlot)}");
            if (GUILayout.Button("›", GUILayout.Width(28)))
                ChangeSourceSlot(1);
            if (GUILayout.Button("Run comparison"))
                StartLayoutAnalysis();
            GUI.enabled = true;
            GUILayout.EndHorizontal();

            if (analysisTask != null)
                GUILayout.Label("Testing the current slot and 31 possible swaps…");
            if (!string.IsNullOrEmpty(analysisMessage))
                GUILayout.Label(analysisMessage);
            if (layoutResult != null)
                DrawLayoutResult(layoutResult, width - 34f);

            GUILayout.EndScrollView();
            GUILayout.EndArea();

            if (layoutResult != null && layoutResult.SourceSlot != layoutResult.RecommendedSlot)
            {
                MarkSlot(layoutResult.SourceSlot);
                MarkSlot(layoutResult.RecommendedSlot);
            }
        }

        private void ChangeSourceSlot(int direction)
        {
            sourceSlot = (sourceSlot + direction + racks.Count) % racks.Count;
            layoutResult = null;
            analysisMessage = null;
        }

        private void DrawLayoutResult(RackLayoutAnalysis.Analysis result, float contentWidth)
        {
            string source = RackLayoutAnalysis.SlotName(result.SourceSlot);
            string target = RackLayoutAnalysis.SlotName(result.RecommendedSlot);
            GUILayout.Label(result.SourceSlot == result.RecommendedSlot
                ? $"Keep {source}. No swap improved the modeled worst case."
                : $"Swap {source} ↔ {target}   ·   Best of 32 tested placements");

            float improvement = result.Candidates[result.SourceSlot].Worst.Peak - result.Candidates[result.RecommendedSlot].Worst.Peak;
            if (improvement > 0.005f)
                GUILayout.Label($"{improvement:F2} °C lower worst-case inlet temperature");

            GUILayout.BeginHorizontal();
            GUILayout.Label("Candidate", GUILayout.Width(143));
            GUILayout.Label("Slot", GUILayout.Width(36));
            GUILayout.Label("Peak inlet", GUILayout.Width(62));
            GUILayout.Label("Above 27 °C", GUILayout.Width(79));
            GUILayout.Label("Worst case", GUILayout.Width(69));
            GUILayout.EndHorizontal();
            DrawResultRow("Current layout", result, result.SourceSlot);
            DrawResultRow("Balance cooling load", result, result.BalancedSlot);
            DrawResultRow("Select with CRACs on", result, result.NormalOnlySlot);
            DrawResultRow("Select for outages", result, result.RecommendedSlot);

            GUILayout.Label("Heatmap at each layout’s peak for the selected case");
            int view = GUILayout.Toolbar(previewSwap ? 1 : 0, LayoutViews, GUILayout.Width(contentWidth));
            int selectedCase = GUILayout.Toolbar(previewCase, analysisCases, GUILayout.Width(contentWidth));
            if (view != (previewSwap ? 1 : 0) || selectedCase != previewCase)
            {
                previewSwap = view == 1;
                previewCase = selectedCase;
                DrawLayoutField();
            }

            int slot = previewSwap ? result.RecommendedSlot : result.SourceSlot;
            RackLayoutAnalysis.ScenarioMetrics preview = result.Candidates[slot].Cases[previewCase];
            string caseName = previewCase < 2 ? analysisCases[previewCase] : $"{analysisCases[previewCase]} offline";
            GUILayout.Label($"{caseName} · Peak {preview.Peak:F1} °C at {RackLayoutAnalysis.SlotName(preview.HottestSlot)}, {preview.PeakSecond} s");
            GUILayout.Label($"{preview.RacksOverLimit} racks above 27 °C · {preview.DegreeMinutes:F1} rack-deg min");
            DrawLegend();
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("Export summary CSV"))
                ExportLayoutResult(result);
            GUI.enabled = traceExportTask == null;
            if (GUILayout.Button(traceExportTask == null ? "Export rack traces CSV" : "Exporting traces…"))
                ExportRackTraces(result);
            GUI.enabled = true;
            GUILayout.EndHorizontal();
        }

        private static readonly string[] LayoutViews = { "Current layout", "Recommended layout" };

        private void DrawResultRow(string label, RackLayoutAnalysis.Analysis result, int slot)
        {
            RackLayoutAnalysis.Candidate candidate = result.Candidates[slot];
            RackLayoutAnalysis.ScenarioMetrics worst = candidate.Worst;
            GUILayout.BeginHorizontal();
            GUILayout.Label(label, GUILayout.Width(143));
            GUILayout.Label(RackLayoutAnalysis.SlotName(slot), GUILayout.Width(36));
            GUILayout.Label($"{worst.Peak:F2} °C", GUILayout.Width(62));
            GUILayout.Label($"{worst.RacksOverLimit} racks", GUILayout.Width(79));
            GUILayout.Label(analysisCases[candidate.WorstIndex], GUILayout.Width(69));
            GUILayout.EndHorizontal();
        }

        private void MarkSlot(int slot)
        {
            Camera camera = Camera.main;
            if (camera == null)
                return;

            Vector3 screen = camera.WorldToScreenPoint(racks[slot].position + Vector3.up * 2.3f);
            if (screen.z <= 0f)
                return;

            float scale = GUI.matrix.lossyScale.x;
            GUI.Box(new Rect(screen.x / scale - 22, (Screen.height - screen.y) / scale - 10, 44, 22),
                RackLayoutAnalysis.SlotName(slot));
        }

        private void ExportLayoutResult(RackLayoutAnalysis.Analysis result)
        {
            string file = $"thermowatch-rack-layout-{DateTime.UtcNow:yyyyMMdd-HHmmss-fff}.csv";
            string path = Path.Combine(Application.persistentDataPath, file);
            try
            {
                Directory.CreateDirectory(Application.persistentDataPath);
                File.WriteAllText(path, result.ToCsv(analysisCases));
                analysisMessage = "CSV saved. See the Unity Console for its path.";
                Debug.Log($"ThermoWatch analysis saved to {path}");
            }
            catch (IOException exception)
            {
                analysisMessage = "Could not save the CSV. See the Unity Console.";
                Debug.LogException(exception);
            }
            catch (UnauthorizedAccessException exception)
            {
                analysisMessage = "Could not write the CSV to the application data folder.";
                Debug.LogException(exception);
            }
        }

        private void ExportRackTraces(RackLayoutAnalysis.Analysis result)
        {
            string directory = Application.persistentDataPath;
            string file = $"thermowatch-rack-traces-{DateTime.UtcNow:yyyyMMdd-HHmmss-fff}.csv";
            string path = Path.Combine(directory, file);
            string[] cases = analysisCases;
            analysisMessage = null;
            traceExportTask = Task.Run(() =>
            {
                Directory.CreateDirectory(directory);
                string pending = path + ".tmp";
                try
                {
                    using (var writer = File.CreateText(pending))
                        result.WriteTraces(writer, cases);
                    File.Move(pending, path);
                    return path;
                }
                finally
                {
                    if (File.Exists(pending))
                        File.Delete(pending);
                }
            });
        }

        private void PollTraceExport()
        {
            if (traceExportTask == null || !traceExportTask.IsCompleted)
                return;

            if (traceExportTask.Status == TaskStatus.RanToCompletion)
            {
                analysisMessage = "Trace CSV saved. See the Unity Console for its path.";
                Debug.Log($"ThermoWatch rack traces saved to {traceExportTask.Result}");
            }
            else
            {
                analysisMessage = "Could not save the trace CSV. See the Unity Console.";
                Debug.LogException(traceExportTask.Exception.GetBaseException());
            }

            traceExportTask = null;
        }

        private void OnDestroy()
        {
            analysisCancellation?.Cancel();
            analysisCancellation?.Dispose();
            Destroy(texture);
            Destroy(legend);
            Destroy(material);
            Destroy(mesh);
        }
    }
}
