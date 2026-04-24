import { Hero } from "@/components/landing/hero";
import { TargetsMarquee } from "@/components/landing/targets-marquee";
import { ModuleShowcase } from "@/components/landing/module-showcase";
import { ComparisonTable } from "@/components/landing/comparison-table";
import { Cta } from "@/components/landing/cta";

export default function HomePage() {
  return (
    <main>
      <Hero />
      <TargetsMarquee />
      <ModuleShowcase />
      <ComparisonTable />
      <Cta />
    </main>
  );
}
