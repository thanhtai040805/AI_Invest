/**
 * ECharts initialization and configuration
 */
import * as echarts from "echarts/core";
import {
  CandlestickChart,
  LineChart,
  BarChart,
  HeatmapChart,
} from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  DataZoomComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

// Register components
echarts.use([
  CandlestickChart,
  LineChart,
  BarChart,
  HeatmapChart,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  DataZoomComponent,
  LegendComponent,
  CanvasRenderer,
]);

export { echarts };
