import type { Metadata, Viewport } from "next";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "HEAT//SHIFT — Urban Heat Decision Intelligence",
  description: "แพลตฟอร์มข่าวกรองเชิงพื้นที่เพื่อการลงทุนลดความร้อนในกรุงเทพมหานคร",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#071016",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="th" className="dark">
      <body>{children}</body>
    </html>
  );
}
