/**
 * Minimal ambient declarations for the Cytoscape plugins we use.
 *
 * ``cytoscape`` itself ships its own .d.ts; only the React wrapper
 * lacks types.
 */

declare module "react-cytoscapejs" {
  import type { ComponentType, CSSProperties, Ref } from "react";
  import type { Core, ElementDefinition, LayoutOptions, StylesheetJsonBlock } from "cytoscape";

  export interface CytoscapeComponentProps {
    elements: ElementDefinition[];
    style?: CSSProperties;
    className?: string;
    layout?: LayoutOptions;
    stylesheet?: StylesheetJsonBlock[];
    cy?: (cy: Core) => void;
    minZoom?: number;
    maxZoom?: number;
    zoom?: number;
    pan?: { x: number; y: number };
    boxSelectionEnabled?: boolean;
    userZoomingEnabled?: boolean;
    userPanningEnabled?: boolean;
    autoungrabify?: boolean;
    autounselectify?: boolean;
    wheelSensitivity?: number;
  }

  const CytoscapeComponent: ComponentType<CytoscapeComponentProps & { ref?: Ref<unknown> }>;
  export default CytoscapeComponent;
}
