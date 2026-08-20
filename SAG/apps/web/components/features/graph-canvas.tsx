"use client";

import * as React from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  ReactFlow,
  useNodesState,
  useNodesInitialized,
  useReactFlow,
  type Edge,
  type Node,
  type NodeTypes,
  type ReactFlowProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ListTree, Maximize2, Minimize2, Orbit, RotateCcw, Share2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

export type GraphLayout = "radial" | "tree" | "force";
export type GraphPoint = { x: number; y: number };
export type GraphSide = "top" | "right" | "bottom" | "left";

export const GRAPH_EDGE_TYPE: Record<GraphLayout, "straight" | "smoothstep"> = {
  radial: "straight",
  tree: "smoothstep",
  force: "straight",
};

const SIDES: Array<{ side: GraphSide; position: Position }> = [
  { side: "top", position: Position.Top },
  { side: "right", position: Position.Right },
  { side: "bottom", position: Position.Bottom },
  { side: "left", position: Position.Left },
];

export function GraphHandles() {
  return (
    <>
      {SIDES.map(({ side, position }) => (
        <React.Fragment key={side}>
          <Handle
            id={`target-${side}`}
            type="target"
            position={position}
            isConnectable={false}
            className="!size-2 !border-0 !bg-transparent !opacity-0"
          />
          <Handle
            id={`source-${side}`}
            type="source"
            position={position}
            isConnectable={false}
            className="!size-2 !border-0 !bg-transparent !opacity-0"
          />
        </React.Fragment>
      ))}
    </>
  );
}

function sideFromVector(from: GraphPoint, to: GraphPoint): GraphSide {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? "right" : "left";
  return dy >= 0 ? "bottom" : "top";
}

function oppositeSide(side: GraphSide): GraphSide {
  if (side === "top") return "bottom";
  if (side === "bottom") return "top";
  if (side === "left") return "right";
  return "left";
}

export function graphEdgeHandles(from: GraphPoint, to: GraphPoint) {
  const side = sideFromVector(from, to);
  return {
    sourceHandle: `source-${side}`,
    targetHandle: `target-${oppositeSide(side)}`,
  };
}

export interface GraphLegendItem {
  label: string;
  className: string;
}

export function GraphLegend({
  items,
  children,
  className,
}: {
  items: GraphLegendItem[];
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "pointer-events-none absolute left-3 top-24 z-10 max-w-[calc(100%-1.5rem)] rounded-lg border bg-card/95 px-2.5 py-2 shadow-soft backdrop-blur-sm sm:top-14",
        className,
      )}
    >
      <div className="flex flex-wrap gap-x-3 gap-y-1.5">
        {items.map((item) => (
          <span
            key={item.label}
            className="inline-flex items-center gap-1.5 text-[10px] text-muted-foreground"
          >
            <span className={cn("size-2 rounded-full", item.className)} />
            {item.label}
          </span>
        ))}
      </div>
      {children}
    </div>
  );
}

function GraphLayoutToggle({
  value,
  onValueChange,
}: {
  value: GraphLayout;
  onValueChange: (layout: GraphLayout) => void;
}) {
  const t = useTranslations("Graph");
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      value={value}
      onValueChange={(next) => next && onValueChange(next as GraphLayout)}
      aria-label={t("layout")}
      className="rounded-md bg-card/95 shadow-soft backdrop-blur-sm"
    >
      <ToggleGroupItem value="force" aria-label={t("forceLayout")} title={t("forceLayout")}>
        <Share2 />
      </ToggleGroupItem>
      <ToggleGroupItem value="radial" aria-label={t("radialLayout")} title={t("radialLayout")}>
        <Orbit />
      </ToggleGroupItem>
      <ToggleGroupItem value="tree" aria-label={t("treeLayout")} title={t("treeLayout")}>
        <ListTree />
      </ToggleGroupItem>
    </ToggleGroup>
  );
}

function FitViewOnChange({
  nodes,
  edges,
  refreshKey,
  padding,
  minZoom,
}: {
  nodes: Node[];
  edges: Edge[];
  refreshKey: unknown;
  padding: number;
  minZoom: number;
}) {
  const { fitView } = useReactFlow();
  const initialized = useNodesInitialized();
  React.useEffect(() => {
    if (!initialized || nodes.length === 0) return;
    let frame = 0;
    const timers: number[] = [];
    const fit = (duration = 260) => {
      fitView({ padding, duration, minZoom, maxZoom: 1.05 });
    };
    frame = window.requestAnimationFrame(() => {
      fit(0);
      timers.push(window.setTimeout(() => fit(), 120));
      timers.push(window.setTimeout(() => fit(), 360));
    });
    return () => {
      window.cancelAnimationFrame(frame);
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [edges, fitView, initialized, minZoom, nodes, padding, refreshKey]);
  return null;
}

const LAYOUT_LABEL_KEY = {
  radial: "radialLayout",
  tree: "treeLayout",
  force: "forceNetwork",
} as const;

const LAYOUT_ICON = {
  radial: Orbit,
  tree: ListTree,
  force: Share2,
};

export function GraphCanvas({
  nodes,
  edges,
  nodeTypes,
  layout,
  onLayoutChange,
  legend,
  toolbarActions,
  children,
  refreshKey,
  heightClassName = "h-[clamp(500px,calc(100svh-14rem),860px)]",
  className,
  flowClassName,
  ariaLabel,
  fitPadding = 0.2,
  fitMinZoom = 0.2,
  minZoom = 0.16,
  maxZoom = 1.7,
  elementsSelectable = false,
  onlyRenderVisibleElements = false,
  onNodeClick,
  onPaneClick,
}: {
  nodes: Node[];
  edges: Edge[];
  nodeTypes: NodeTypes;
  layout: GraphLayout;
  onLayoutChange: (layout: GraphLayout) => void;
  legend: React.ReactNode;
  toolbarActions?: React.ReactNode;
  children?: React.ReactNode;
  refreshKey?: unknown;
  heightClassName?: string;
  className?: string;
  flowClassName?: string;
  ariaLabel: string;
  fitPadding?: number;
  fitMinZoom?: number;
  minZoom?: number;
  maxZoom?: number;
  elementsSelectable?: boolean;
  onlyRenderVisibleElements?: boolean;
  onNodeClick?: ReactFlowProps["onNodeClick"];
  onPaneClick?: ReactFlowProps["onPaneClick"];
}) {
  const t = useTranslations("Graph");
  const [mounted, setMounted] = React.useState(false);
  const [expanded, setExpanded] = React.useState(false);
  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState(nodes);
  const [hoveredNodeId, setHoveredNodeId] = React.useState<string | null>(null);
  const [positionVersion, setPositionVersion] = React.useState(0);

  React.useEffect(() => setMounted(true), []);

  React.useEffect(() => {
    setFlowNodes(nodes);
    setHoveredNodeId(null);
  }, [nodes, setFlowNodes]);

  const connectedNodeIds = React.useMemo(() => {
    if (!hoveredNodeId) return null;
    const connected = new Set([hoveredNodeId]);
    edges.forEach((edge) => {
      if (edge.source === hoveredNodeId) connected.add(edge.target);
      if (edge.target === hoveredNodeId) connected.add(edge.source);
    });
    return connected;
  }, [edges, hoveredNodeId]);

  const renderedNodes = React.useMemo(() => {
    if (!connectedNodeIds) return flowNodes;
    return flowNodes.map((node) => ({
      ...node,
      style: {
        ...node.style,
        opacity: connectedNodeIds.has(node.id) ? 1 : 0.22,
      },
    }));
  }, [connectedNodeIds, flowNodes]);

  const renderedEdges = React.useMemo(() => {
    const positions = new Map(flowNodes.map((node) => [node.id, node.position]));
    return edges.map((edge) => {
      const from = positions.get(edge.source) ?? { x: 0, y: 0 };
      const to = positions.get(edge.target) ?? { x: 0, y: 0 };
      const highlighted = Boolean(
        hoveredNodeId && (edge.source === hoveredNodeId || edge.target === hoveredNodeId),
      );
      const muted = Boolean(hoveredNodeId && !highlighted);
      const strokeWidth =
        typeof edge.style?.strokeWidth === "number" ? edge.style.strokeWidth : 1.25;
      return {
        ...edge,
        ...graphEdgeHandles(from, to),
        zIndex: highlighted ? 2 : edge.zIndex,
        style: {
          ...edge.style,
          opacity: muted ? 0.1 : 1,
          strokeWidth: highlighted ? strokeWidth + 0.85 : strokeWidth,
        },
      };
    });
  }, [edges, flowNodes, hoveredNodeId]);

  const resetNodePositions = React.useCallback(() => {
    setFlowNodes(nodes);
    setHoveredNodeId(null);
    setPositionVersion((value) => value + 1);
  }, [nodes, setFlowNodes]);

  React.useEffect(() => {
    if (!expanded) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [expanded]);

  const LayoutIcon = LAYOUT_ICON[layout];
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-lg border bg-card/40",
        expanded ? "fixed inset-4 z-50 min-h-0 bg-card shadow-lift" : heightClassName,
        className,
      )}
    >
      {legend}
      <div className="absolute right-3 top-3 z-20 flex w-[calc(100%-1.5rem)] flex-wrap items-center justify-end gap-1.5 sm:w-auto sm:max-w-[calc(100%-1.5rem)]">
        <GraphLayoutToggle value={layout} onValueChange={onLayoutChange} />
        <button
          type="button"
          onClick={resetNodePositions}
          disabled={nodes.length === 0}
          aria-label={t("resetPositions")}
          title={t("resetPositions")}
          className="grid size-8 place-items-center rounded-md border bg-card/95 text-muted-foreground shadow-soft outline-none backdrop-blur-sm transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40"
        >
          <RotateCcw className="size-4" />
        </button>
        {toolbarActions}
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-label={expanded ? t("exitGraphFullscreen") : t("graphFullscreen")}
          title={expanded ? t("exitFullscreen") : t("fullscreen")}
          className="grid size-8 place-items-center rounded-md border bg-card/95 text-muted-foreground shadow-soft outline-none backdrop-blur-sm transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        >
          {expanded ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
        </button>
      </div>

      {mounted ? (
        <ReactFlow
          nodes={renderedNodes}
          edges={renderedEdges}
          nodeTypes={nodeTypes}
          nodeOrigin={[0.5, 0.5]}
          fitView
          fitViewOptions={{ padding: fitPadding, minZoom: fitMinZoom, maxZoom: 1.05 }}
          minZoom={minZoom}
          maxZoom={maxZoom}
          proOptions={{ hideAttribution: true }}
          nodesDraggable
          onNodesChange={onNodesChange}
          nodesConnectable={false}
          elementsSelectable={elementsSelectable}
          onlyRenderVisibleElements={onlyRenderVisibleElements}
          onNodeClick={onNodeClick}
          onNodeMouseEnter={(_event, node) => setHoveredNodeId(node.id)}
          onNodeMouseLeave={() => setHoveredNodeId(null)}
          onPaneClick={(event) => {
            setHoveredNodeId(null);
            onPaneClick?.(event);
          }}
          aria-label={ariaLabel}
          className={flowClassName}
        >
          <FitViewOnChange
            nodes={nodes}
            edges={edges}
            refreshKey={`${expanded}-${layout}-${positionVersion}-${String(refreshKey ?? "")}`}
            padding={fitPadding}
            minZoom={fitMinZoom}
          />
          <Background
            variant={BackgroundVariant.Dots}
            gap={22}
            size={1}
            className="!bg-transparent"
          />
          <Controls showInteractive={false} className="!shadow-soft" />
        </ReactFlow>
      ) : (
        <div className="absolute inset-0 bg-card/20" aria-hidden="true" />
      )}

      {children}
      <div className="pointer-events-none absolute bottom-3 right-3 z-10 flex items-center gap-1 rounded-md border bg-card/90 px-2 py-1 text-[10px] text-muted-foreground shadow-soft backdrop-blur-sm">
        <LayoutIcon className="size-3" />
        {t(LAYOUT_LABEL_KEY[layout])}
      </div>
    </div>
  );
}
