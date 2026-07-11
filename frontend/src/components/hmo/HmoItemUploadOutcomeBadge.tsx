import {UploadOutcomeBadge, type UploadOutcomeBadgeProps} from "@/components/shared/UploadOutcomeBadge";

export type HmoItemUploadOutcomeBadgeProps = Omit<UploadOutcomeBadgeProps, "testIdPrefix" | "neverTriedLabel">;

export function HmoItemUploadOutcomeBadge(props: HmoItemUploadOutcomeBadgeProps) {
  return <UploadOutcomeBadge {...props} testIdPrefix="hmo-item" neverTriedLabel="never tried" />;
}
