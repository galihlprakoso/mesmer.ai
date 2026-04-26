import { Hero } from "@/components/landing/hero";
import { TargetsMarquee } from "@/components/landing/targets-marquee";
import { GraphDemo } from "@/components/landing/graph-demo";
import { ModuleShowcase } from "@/components/landing/module-showcase";
import { ComparisonTable } from "@/components/landing/comparison-table";
import { Cta } from "@/components/landing/cta";
import { Footer } from "@/components/landing/footer";

export default function HomePage() {
  return (
    <main>
      <Hero />
      <TargetsMarquee />
      <GraphDemo />
      <ModuleShowcase />
      <ComparisonTable />
      <Cta />
      <Footer />
    </main>
  );
}
