/// <reference types="vite/client" />

declare module "react-grid-layout" {
  import type { ComponentType } from "react";

  export type Layout = unknown;
  export const WidthProvider: (component: unknown) => ComponentType<any>;
  const GridLayout: ComponentType<any>;
  export default GridLayout;
}
