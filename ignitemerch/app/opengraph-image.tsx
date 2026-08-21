import { ImageResponse } from "next/og";

export const runtime = "edge";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "flex-end",
          background: "#2a1810",
          color: "#f3ead8",
          padding: 72,
        }}
      >
        <div style={{ fontSize: 28, letterSpacing: 8, color: "#e07a2f" }}>
          IGNITE MERCH
        </div>
        <div style={{ fontSize: 72, fontWeight: 800, lineHeight: 0.9, marginTop: 16 }}>
          KITS YOU CAN RUN TONIGHT
        </div>
      </div>
    ),
    size,
  );
}
