import torch 
import torch.nn.functional as F
import torch.nn as nn
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")
import copy
import torch.optim as optim
import model_architectures


def fgsm_attack(image, epsilon, data_grad):
    """
    Author: Sai Coumar
    Description: Perturbs an image using the Fast Gradient Sign Method attack
    Attack Type: White Box

    Parameters:
    - image: The pytorch tensor of an image to perturb
    - epsilon: A constant used to control the magnitude of perturbation

    Returns:
    - Perturbed image

    Literature:
    - https://arxiv.org/abs/1412.6572
    """
    # Collect the element-wise sign of the data gradient
    sign_data_grad = data_grad.sign()
    # Create the perturbed image by adjusting each pixel of the input image
    perturbed_image = image + epsilon*sign_data_grad
    # Clip values to 0-255 to maintain pixel value range
    torch.clamp(perturbed_image, 0, 255)
    # Return the perturbed image
    return perturbed_image

def deepfool_attack(image, model, overshoot=0.02, max_iterations=50):  
    """
    Author: Sai Coumar
    Description: Perturbs an image using the DeepFool attack
    Attack Type: White Box

    Parameters:
    - image: The pytorch tensor of an image to perturb
    - model: The classifier model to attack
    - overshoot: Hyperparameter to edge the perturbation past the minimal amount needed 
    to perturb the image just to be safe the perturbation crosses the decision boundary
    - max_iterations: Hyperparameter to limit resources to finite value 

    Returns:
    - Perturbed image

    Literature:
    - https://www.cv-foundation.org/openaccess/content_cvpr_2016/papers/Moosavi-Dezfooli_DeepFool_A_Simple_CVPR_2016_paper.pdf
    """
    # Copy the image data as an object to preserve gradient. This will be perturbed 
    # rather than the original image data
    x = copy.deepcopy(image)

    # Get the actual predictions from the model
    output_actual, _ = model(x)
    # Probability of the label's classification
    # print(output_actual)
    

    # Actual label value (number)
    _, label_actual = torch.max(output_actual.data, 1)
    label_probability = output_actual[0][label_actual].item()
    # Store reference of original image
    original_img = copy.deepcopy(image)
    # Initialize weights w
    w = torch.zeros_like(image)
    # Initialize perturbations
    r_total = torch.zeros_like(image)

    # Variables to limit perturbation. Perturbation ends after a fixed number of iterations
    # or when the poisoned images' predicted label no longer matches the clean images' predicted label
    iter = 0
    k_i = label_actual

    while k_i == label_actual and iter < max_iterations:
        iter += 1
        
        # Argmin l
        l = float('inf')
        output_actual[:, label_actual].backward(retain_graph=True)
        # Extract Gradient of Image w.r.t. true prediction
        grad_original = x.grad.clone() 

        for k, class_prob in enumerate(output_actual.squeeze()):
            # Check all other class predictions
            if k != label_actual.item():
                # Extract Gradient of Image w.r.t. every other class except predicted
                model.zero_grad()
                output_actual[:, k].backward(retain_graph=True)
                x.retain_grad()
                curr_grad = x.grad.clone()
                # calculate l for the current class
                w_k = curr_grad - grad_original
                f_k = class_prob.item() - label_probability

                l_k = abs(f_k)/ torch.norm(w_k.view(-1))
                # pick minimum l
                if l_k < l:
                    l = l_k
                    w = w_k

        # Calculate the perturbation
        r_i = (1 + 1e-4) * w / torch.norm(w.view(-1))
        r_total += r_i

        # Perturb image
        x = original_img + (1+overshoot)*r_total
        x.retain_grad()
        output_actual, _ = model(x)
        _, k_i = torch.max(output_actual.data, 1)

    return x, k_i, r_total, iter

def pgd_attack(image, model, init_pred, epsilon, alpha=2,  max_iterations=50):
    """
    Author: Sai Coumar
    Description: Perturbs an image using the Projected Gradient Descent attack
    Attack Type: White Box

    Parameters:
    - image: The pytorch tensor of an image to perturb
    - model: The classifier model to attack
    - init_pred: True classified label given by the model before any attacks
    - epsilon: A hyperparameter that defines the epsilon-ball threshold that the perturbed image
    must stay confined to in order to retain percievability
    - alpha: Step size hyperparameter to control the magnitude of perturbation
    - max_iterations: Hyperparameter to limit resources to finite value  

    Returns:
    - Perturbed image

    Literature:
    - https://arxiv.org/abs/1412.6572
    """

    # Note: other examples may use alpha and epsilon divided by 255 
    # because they normalize pixels to 1.
    # Since normalization wasn't used we use integers 
    perturbed_image = image
    output_final = None
    for _ in range(max_iterations):
        # Predict on perturbed image
        output, _ = model(perturbed_image)
        output_final = output
        # _, pred = torch.max(output.data, 1)

        # Compare loss of true prediction vs outputted prediction
        loss = F.cross_entropy(output, init_pred)
        model.zero_grad()
        loss.backward() 

        # Extract gradient
        sign_data_grad = image.grad.data.sign()
        # print(sign_data_grad.size())

        # Perturb image
        perturbed_image = image  + alpha * sign_data_grad
        
        # Clipping to epislon ball
        eta = torch.clamp(perturbed_image - image, min=-epsilon, max=epsilon)
        perturbed_image = torch.clamp(perturbed_image + eta, min=0, max=1)

    return output_final, perturbed_image

def nes_attack(image, model, init_pred, init_labels, epsilon, alpha=2,  max_iterations=50):
    """
    Author: Sai Coumar
    Description: Perturbs an image using the Natural Evolution Strategies (Finite Differences Variant) attack
    Attack Type: Score-Based Black Box

    Parameters:
    - image: The pytorch tensor of an image to perturb
    - model: The classifier model to attack
    - init_pred: True classified label given by the model before any attacks
    - epsilon: A hyperparameter that defines the epsilon-ball threshold that the perturbed image
    must stay confined to in order to retain percievability
    - alpha: Step size hyperparameter to control the magnitude of perturbation
    - max_iterations: Hyperparameter to limit resources to finite value  

    Returns:
    - Perturbed image

    Literature:
    - https://arxiv.org/pdf/1804.08598.pdf
    """
    def create_multivariate_gaussian(mean, cov_matrix):
        # Create a MultivariateNormal distribution with the specified mean and covariance matrix
        mv_normal = torch.distributions.multivariate_normal.MultivariateNormal(mean, cov_matrix)
        # Sample from the distribution
        sample = mv_normal.sample()
        return sample
        
    def nes_estimation(sign_data_grad_actual, model, label_actual, sigma, n, image):
        N = image.size()[2]

        g = torch.zeros(1, 1, N, N)
        g = g.to(device)
        # print(g.size())
        for _ in range(n):
            
            ui = create_multivariate_gaussian(torch.zeros(N), torch.eye(N)).unsqueeze(0).unsqueeze(0)
            ui = ui.to(device)

            p_plus = model(image + sigma * ui)[0][0][label_actual].item()
            p_minus = model(image - sigma * ui)[0][0][label_actual].item()

            g += p_plus * ui
            g -= p_minus * ui
            
            # print("diff: ", torch.norm((sign_data_grad_actual - g).view(-1)).item(), " ,prob +: ", p_plus, " ,prob -: ", p_minus)
          
        return (1 / (2 * n * sigma)) * g

    
    # # Note: other examples may use alpha and epsilon divided by 255 
    # # because they normalize pixels to 1.
    # # Since normalization wasn't used we use integers 
    perturbed_image = image
    output_final = None
    

    for _ in range(max_iterations):
        # Predict on perturbed image
        output, _ = model(perturbed_image)
        output_final = output

        _, label_actual = torch.max(output.data, 1)
        label_probability = output[0][init_pred].item()
        # print(label_probability)
        loss = F.cross_entropy(output, init_pred)
        model.zero_grad()
        loss.backward() 

        # Extract gradient
        sign_data_grad_actual = image.grad.data.sign()
        # Extract gradient
        sign_data_grad = nes_estimation(sign_data_grad_actual, model, init_pred, sigma=0.001, n=100, image = perturbed_image)
        sign_data_grad = sign_data_grad.to(device)
        # print(torch.norm((sign_data_grad_actual - sign_data_grad).view(-1)))
        print(label_actual[0].item(), init_pred[0].item(), label_probability, " ,diff: ", torch.norm((sign_data_grad_actual - sign_data_grad).view(-1)).item())

        # Perturb image
        perturbed_image = image + alpha * sign_data_grad
        
        # Clipping to epislon ball
        eta = torch.clamp(perturbed_image - image, min=-epsilon, max=epsilon)
        perturbed_image = torch.clamp(perturbed_image + eta, min=0, max=1)
        # print(perturbed_image)

    return output_final, perturbed_image

def cw_attack(images, model, labels, targeted=False, target_labels = 0, c=0.1, alpha=0.01, kappa=0, max_iterations=50):
    """
    Author: Supriya Dixit
    Description: Perturbs an image using the Carlini and Wagner attack
    Attack Type: White Box

    Parameters:
    - image: The pytorch tensor of an image to perturb
    - model: The classifier model to attack
    - c: some constant c that lets you control how much influence the "maximum allowable" portion has
    - alpha: learning rate of adam optimizer
    - label: label of the image passed in


    Returns:
    - Perturbed image

    Literature:
    - https://arxiv.org/abs/1608.04644
    """
    
    viz = model_architectures.Visualizer()
    
    images = images.clone().detach().to(device)     
    labels = labels.clone().detach().to(device)
    
    
    MSELoss = nn.MSELoss(reduction="none")
    Flatten = nn.Flatten()
    
    best_adv_images = images.clone().detach()
    best_L2 = 1e10 * torch.ones((len(images))).to(device)
    dim = len(images.shape)

    w = torch.zeros_like(images).detach()
    w.requires_grad = True

    adam = optim.Adam([w], lr=alpha)
    for _ in range(max_iterations):
        #adam.zero_grad()
        tanh_images = 1/2 * (torch.tanh(w) + 1)
        

        #tanh_images = tanh_images/255
        ########################### f-function here #################################
        
        # f(x′)= max(max{Z(x′)i : i!=t}−Z(x′)t,−κ)
        # max of (max of all other non target classes - the target class) and -kappa
        # kappa - confidence with which the misclassification occurs 
        
        # prediction BEFORE-SOFTMAX of the model
        outs, _ = model(tanh_images) #outs[1] is the probability of each class
        
        if targeted:
            labels_encoded = torch.eye(outs.shape[1]).to(device)[labels]
        else:
            labels_encoded = F.one_hot(labels, 10)
        #print(outs[0])
        
        other = torch.max((1 - labels_encoded) * outs, dim=1)
        #real = torch.masked_select(outs, labels_encoded.byte())
        real = torch.max(labels_encoded * outs, dim=1)
        

        if targeted: # If targeted, optimize for making the other class most likely
            a = torch.clamp(other[0] - real[0], min=-kappa)
        else:# If untargeted, optimize for making the other class most likely 
            a = torch.clamp(real[0] - other[0], min=-kappa)
            
        #############################################################################
        

        current_L2 = MSELoss(Flatten(tanh_images), Flatten(images)).sum()
        costp1 = current_L2.sum()

        costp2 = c * torch.sum(a)

        cost = costp1 + costp2

        #do a step of gradient descent on w
        adam.zero_grad()
        cost.backward()
        adam.step()
        
        # Update adversarial images
        pre = torch.argmax(outs.detach(), 1)
        
        if targeted:
            #We want to let pre == target_labels in a targeted attack
            condition = (pre == target_labels).float()
        else:
            # If the attack is not targeted we simply make these two values unequal
            condition = (pre != labels).float()
        
        mask = condition * (best_L2 > current_L2.detach())
        best_L2 = mask * current_L2.detach() + (1 - mask) * best_L2

        mask = mask.view([-1] + [1] * (dim - 1))
        best_adv_images = mask * tanh_images.detach() + (1 - mask) * best_adv_images
   


    #return
    return best_adv_images

        
def jsma_attack( model, input_image, target_class, num_classes, theta=0.1, gamma=0.1, max_iters=100):
    """
    Author: Supriya Dixit
    Description: Perturbs an image using the JSMA attack
    Attack Type: White Box

    Parameters:
    - image: The pytorch tensor of an image to perturb
    

    Returns:
    - Perturbed image
    """
    
    model.eval()

    # Copy the input image to avoid modifying the original image
    adv_image = input_image.clone().detach().requires_grad_(True)
    # Define the optimizer
    optimizer = optim.Adam([adv_image], lr=0.01)

    # Loop until the maximum number of iterations is reached
    for _ in range(max_iters):
        # Forward pass to get the model's predictions
        predictions = model(adv_image)

        # Calculate the loss (targeted attack)
        loss = -nn.CrossEntropyLoss()(predictions[1], torch.tensor([target_class]).to(device))

        # Zero gradients, perform a backward pass, and update the adversarial image
        optimizer.zero_grad()
        loss.backward()
        adv_image.grad.sign_()
        
        adv_image.data = torch.clamp(adv_image + theta * adv_image.grad, 0, 1)
        # Check if the adversarial image is misclassified
        if torch.argmax(model(adv_image)[1]) == target_class:
            break

    return adv_image.detach()

# def boundary_attack(model, input_image, target_image):
#     def get_diff(a,b):
#         return torch.norm((a-b).view(-1))
    

  
    # def nes_estimation(sign_data_grad_actual, model, label_actual, labels, sigma, n, image):
    #     N = image.size()[2]
    #     grads = []
    #     final_losses = []


    #     g = torch.zeros(1, 1, N, N)
    #     g = g.to(device)
    #     # print(g.size())
    #     for _ in range(n):
            
    #         ui = create_multivariate_gaussian(torch.zeros(N), torch.eye(N)).unsqueeze(0).unsqueeze(0)
    #         ui = ui.to(device)
    #         # print(model(image + sigma * ui)[0][0])
    #         print(ui.size())
    #         noise = torch.cat([ui, -ui], dim=0)
    #         print(noise.size())
    #         eval_points = image + sigma * noise
    #         output, _ = model(eval_points)
    #         print(output, labels)
    #         loss = F.cross_entropy(output, labels)
    #         print(loss)
    #         losses_tiled = loss.view(1, 1, 1, 1).expand(1,1,N,N)
    #         print(losses_tiled)
    #         grads.append(losses_tiled * noise)
    #         # g = (grad_plus - grad_minus) * ui
    #         # grads.append(g)
    #         # print("diff: ", torch.norm((sign_data_grad_actual - g).view(-1)).item(), " ,prob +: ", grad_plus, " ,prob -: ", grad_minus)
            
    #     g = torch.sum(torch.stack(grads), dim=0)
    #     return (1 / (2 * n * sigma)) * g

    # perturbed_image = image
    # output_final = None
    # for _ in range(max_iterations):
    #     # Predict on perturbed image
    #     output, _ = model(perturbed_image)
    #     output_final = output
    #     # _, pred = torch.max(output.data, 1)

    #     # Compare loss of true prediction vs outputted prediction
        
    #     # Extract gradient
    #     sign_data_grad = nes_estimation(model, init_pred, sigma=0.001, n=50, image = perturbed_image)
    #     # print(sign_data_grad.size())

    #     # Perturb image
    #     perturbed_image = image  + alpha * sign_data_grad
        
    #     # Clipping to epislon ball
    #     eta = torch.clamp(perturbed_image - image, min=-epsilon, max=epsilon)
    #     perturbed_image = torch.clamp(perturbed_image + eta, min=0, max=255)

    # return output_final, perturbed_image