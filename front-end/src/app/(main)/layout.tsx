import Sidebar from "@/components/layout/Sidebar";
import { PageTransition } from "@/components/layout/PageTransition";

export default function MainLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar />
      <main className="flex-1 overflow-y-auto relative bg-background scroll-smooth">
        <PageTransition>
          {children}
        </PageTransition>
      </main>
    </div>
  );
}
