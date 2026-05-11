"""Scale calculation strategy: unified scale computation across modules."""


class ScaleStrategy:
    """Encapsulates scale calculation logic used throughout GIF compression."""

    @staticmethod
    def compute_suggested_scale(current_scale, target_size, current_size, allow_zero=False):
        """
        Compute suggested scale using geometric mean formula.
        
        Formula: suggested = current * (target / current_size) ** 0.5
        
        Args:
            current_scale: Current scale value (> 0)
            target_size: Target size to reach
            current_size: Current output size
            allow_zero: If False, clamp to valid positive range
            
        Returns:
            Suggested scale value
        """
        if current_size <= 0:
            return current_scale if allow_zero else max(0.01, current_scale)
        if target_size <= 0:
            return current_scale if allow_zero else max(0.01, current_scale)
        
        suggested = current_scale * (target_size / current_size) ** 0.5
        return suggested if allow_zero else max(0.01, suggested)

    @staticmethod
    def apply_step_cap(current_scale, suggested_scale, max_step_ratio):
        """
        Limit the step size between current and suggested scale.
        
        Prevents overshooting when scale changes are too aggressive.
        
        Args:
            current_scale: Current scale
            suggested_scale: Proposed scale
            max_step_ratio: Maximum step as ratio of current scale (e.g., 0.15 = 15%)
            
        Returns:
            Capped scale value
        """
        if max_step_ratio <= 0:
            return current_scale
        
        max_step = current_scale * max_step_ratio
        if abs(suggested_scale - current_scale) <= max_step:
            return suggested_scale
        
        direction = 1 if suggested_scale > current_scale else -1
        return current_scale + direction * max_step

    @staticmethod
    def clamp_to_bracket(suggested_scale, low_scale, high_scale):
        """
        Constrain scale to bracket bounds, using midpoint as fallback.
        
        Args:
            suggested_scale: Proposed scale
            low_scale: Lower bracket bound
            high_scale: Upper bracket bound
            
        Returns:
            Clamped scale value within bracket
        """
        if low_scale >= high_scale:
            return (low_scale + high_scale) / 2.0
        
        if low_scale < suggested_scale < high_scale:
            return suggested_scale
        
        return (low_scale + high_scale) / 2.0

    @staticmethod
    def compute_safe_scale(current_scale, target_size, current_size, low_scale, high_scale, max_step_ratio):
        """
        Compute next scale with all guards applied: formula, step cap, and bracket clamp.
        
        Convenience method that chains: suggested -> capped -> bracketed.
        
        Args:
            current_scale: Current scale
            target_size: Target size
            current_size: Current output size
            low_scale: Lower bracket bound
            high_scale: Upper bracket bound
            max_step_ratio: Max step ratio (e.g., 0.15 for 15%)
            
        Returns:
            Safe scale value with all constraints applied
        """
        suggested = ScaleStrategy.compute_suggested_scale(
            current_scale, target_size, current_size, allow_zero=False
        )
        capped = ScaleStrategy.apply_step_cap(current_scale, suggested, max_step_ratio)
        bracketed = ScaleStrategy.clamp_to_bracket(capped, low_scale, high_scale)
        return bracketed
