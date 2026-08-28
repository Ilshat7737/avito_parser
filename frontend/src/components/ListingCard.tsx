import { motion } from "framer-motion";
import type { Listing } from "../types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

function formatNumber(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("ru-RU");
}

export function ListingCard({ listing }: { listing: Listing }) {
  const priceChanged = listing.last_price !== null && listing.price !== listing.last_price;

  return (
    <motion.a
      href={listing.url}
      target="_blank"
      rel="noreferrer"
      layout
      initial={{ opacity: 0, y: 16, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 260, damping: 20 }}
      className="block hover:scale-[1.02] transition-transform"
    >
      <Card className="rounded-2xl border-t-2 border-t-primary shadow-lg shadow-black/20 overflow-hidden">
        {listing.photo && (
          <img
            src={listing.photo}
            alt={listing.title}
            className="w-full h-40 object-cover"
            loading="lazy"
          />
        )}
        <CardContent className="p-4">
          <h3 className="font-sans font-semibold text-base mb-1 line-clamp-2">{listing.title}</h3>
          <p
            className={`font-tabular text-xl font-medium mb-2 ${priceChanged ? "animate-price-flash rounded px-1" : ""}`}
          >
            {formatNumber(listing.price)} ₽
          </p>
          <div className="flex gap-2 mb-2">
            {listing.year && <Badge variant="secondary" className="font-tabular">{listing.year}</Badge>}
            {listing.mileage !== null && (
              <Badge variant="secondary" className="font-tabular">
                {formatNumber(listing.mileage)} км
              </Badge>
            )}
          </div>
          {listing.relevance_reason && (
            <p className="text-sm text-muted-foreground">{listing.relevance_reason}</p>
          )}
        </CardContent>
      </Card>
    </motion.a>
  );
}
