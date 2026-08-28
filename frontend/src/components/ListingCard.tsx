import { motion } from "framer-motion";
import type { Listing, SessionId } from "../types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

function formatNumber(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("ru-RU");
}

export function ListingCard({ listing, session }: { listing: Listing; session: SessionId }) {
  const priceChanged = listing.last_price !== null && listing.price !== listing.last_price;
  const accentVar = session === 1 ? "--session-1" : "--session-2";

  return (
    <motion.a
      href={listing.url}
      target="_blank"
      rel="noreferrer"
      layout
      initial={{ opacity: 0, y: 16, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={{ type: "spring", stiffness: 260, damping: 20 }}
      className="group block"
    >
      <Card
        className="overflow-hidden rounded-2xl border-t-[3px] shadow-md transition-all duration-200 group-hover:-translate-y-1 group-hover:shadow-xl"
        style={{ borderTopColor: `hsl(var(${accentVar}))` }}
      >
        <div className="relative">
          {listing.photo ? (
            <img
              src={listing.photo}
              alt={listing.title}
              className="h-40 w-full object-cover"
              loading="lazy"
            />
          ) : (
            <div className="flex h-40 w-full items-center justify-center bg-[hsl(var(--foreground))]/5 text-xs text-[hsl(var(--foreground))]/40">
              нет фото
            </div>
          )}
        </div>
        <CardContent className="p-4">
          <h3 className="font-sans font-semibold text-base mb-1 line-clamp-2">{listing.title}</h3>
          <p
            className={`font-mono text-xl font-medium mb-2 ${priceChanged ? "animate-price-flash rounded px-1" : ""}`}
          >
            {formatNumber(listing.price)} ₽
          </p>
          <div className="flex flex-wrap gap-2 mb-2">
            {listing.year && <Badge variant="secondary" className="font-mono">{listing.year}</Badge>}
            {listing.mileage !== null && (
              <Badge variant="secondary" className="font-mono">
                {formatNumber(listing.mileage)} км
              </Badge>
            )}
          </div>
          {listing.relevance_reason && (
            <p className="text-sm text-[hsl(var(--foreground))]/70">{listing.relevance_reason}</p>
          )}
        </CardContent>
      </Card>
    </motion.a>
  );
}
