import Sidebar from "@/components/layout/Sidebar";
import { PageTransition } from "@/components/layout/PageTransition";
import MarketTickerBar from "@/components/layout/MarketTickerBar";

export default function MainLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="flex flex-col min-h-dvh bg-[var(--color-background)]">
      {/* Slim live market ticker at the very top */}
      <MarketTickerBar />

      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main
          id="main-content"
          className="flex-1 overflow-y-auto relative bg-[var(--color-background)] scroll-smooth"
        >
          <PageTransition>{children}</PageTransition>
        </main>
      </div>
    </div>
  );
}
