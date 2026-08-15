package org.enveloping.ecobin.device.web.v1.enrollment;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.enrollment.DeviceEnrollmentService;
import org.enveloping.ecobin.device.web.v1.enrollment.DeviceEnrollmentModels.ChallengeView;
import org.enveloping.ecobin.device.web.v1.enrollment.DeviceEnrollmentModels.EnrollmentRequest;
import org.enveloping.ecobin.device.web.v1.enrollment.DeviceEnrollmentModels.EnrollmentView;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
public class DeviceEnrollmentController {

    private final DeviceEnrollmentService enrollments;

    public DeviceEnrollmentController(DeviceEnrollmentService enrollments) {
        this.enrollments = enrollments;
    }

    @PostMapping("/device-enrollment/challenges")
    public ResponseEntity<ChallengeView> challenge(
            HttpServletRequest request) {
        return ResponseEntity.status(201)
                .cacheControl(CacheControl.noStore())
                .body(enrollments.createChallenge(request.getRemoteAddr()));
    }

    @PostMapping("/device-enrollments")
    public ResponseEntity<EnrollmentView> enroll(
            @Valid @RequestBody EnrollmentRequest body) {
        EnrollmentView result = enrollments.submit(body);
        int status = switch (result.status()) {
            case "PENDING" -> 202;
            case "READY" -> 200;
            case "FAILED" -> 422;
            default -> throw new IllegalStateException(
                    "unsupported enrollment status");
        };
        return ResponseEntity.status(status)
                .cacheControl(CacheControl.noStore())
                .body(result);
    }
}
